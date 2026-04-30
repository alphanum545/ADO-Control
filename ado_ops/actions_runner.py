"""Command-line runner used by GitHub Actions."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_ids(raw: str) -> list[int]:
    """Parse comma, space, or newline separated work item IDs."""
    ids: list[int] = []
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError as exc:
            raise ValueError(f"Invalid work item ID: {token}") from exc
    return ids


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _constraints(args: argparse.Namespace) -> dict[str, Any]:
    from ado_ops.settings import settings

    return {
        "max_actions": args.max_actions,
        "allow_delete": args.allow_delete,
        "allow_project_delete": args.allow_project_delete,
        "hard_delete": args.hard_delete,
        "default_work_item_assignee": settings.default_work_item_assignee,
        "notes": [
            "GitHub Actions plan job always dry-runs first.",
            "Apply runs from the validated plan artifact only after the apply job starts.",
        ],
    }


def _format_actions(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "_No actions planned._"
    rows = [
        "| # | Resource | Operation | Project | Target | Name | Status |",
        "| - | -------- | --------- | ------- | ------ | ---- | ------ |",
    ]
    for index, action in enumerate(actions, start=1):
        status = "skipped" if action.get("skipped") else "ready"
        if action.get("skip_reason"):
            status = f"{status}: {action['skip_reason']}"
        rows.append(
            "| {index} | {resource} | {operation} | {project} | {target} | {name} | {status} |".format(
                index=index,
                resource=_cell(action.get("resource", "")),
                operation=_cell(action.get("operation", "")),
                project=_cell(action.get("project", "")),
                target=_cell(action.get("target", "")),
                name=_cell(action.get("name", "")),
                status=_cell(status),
            )
        )
    return "\n".join(rows)


def _format_results(results: dict[str, Any]) -> str:
    rows_data = results.get("results", [])
    if not isinstance(rows_data, list) or not rows_data:
        return "_No results._"
    rows = [
        "| # | Resource | Operation | Project | Target | Status | Message |",
        "| - | -------- | --------- | ------- | ------ | ------ | ------- |",
    ]
    for index, result in enumerate(rows_data, start=1):
        rows.append(
            "| {index} | {resource} | {operation} | {project} | {target} | {status} | {message} |".format(
                index=index,
                resource=_cell(result.get("resource", "")),
                operation=_cell(result.get("operation", "")),
                project=_cell(result.get("project", "")),
                target=_cell(result.get("target", "")),
                status=_cell(result.get("status", "")),
                message=_cell(result.get("message", "")),
            )
        )
    return "\n".join(rows)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _plan_markdown(bundle: dict[str, Any]) -> str:
    normalized_plan = bundle.get("normalized_plan", {})
    dry_run = bundle.get("dry_run", {})
    warnings = normalized_plan.get("warnings", [])
    warning_block = "\n".join(f"- {warning}" for warning in warnings) if warnings else "_None._"
    return "\n\n".join(
        [
            "# ADO Agent Plan",
            f"Generated at: `{bundle.get('generated_at', '')}`",
            "## Objective",
            str(bundle.get("objective", "")).strip() or "_No objective supplied._",
            "## Summary",
            str(normalized_plan.get("summary", "")).strip() or "_No summary returned._",
            "## Warnings",
            warning_block,
            "## Validated Actions",
            _format_actions(normalized_plan.get("actions", [])),
            "## Dry Run Results",
            _format_results(dry_run),
        ]
    )


def _apply_markdown(payload: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            "# ADO Agent Apply Results",
            f"Applied at: `{payload.get('applied_at', '')}`",
            "## Objective",
            str(payload.get("objective", "")).strip() or "_No objective supplied._",
            "## Results",
            _format_results(payload.get("apply_results", {})),
        ]
    )


def _assert_safe_to_apply(bundle: dict[str, Any]) -> None:
    constraints = bundle.get("constraints", {})
    normalized_plan = bundle.get("normalized_plan", {})
    actions = normalized_plan.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("Plan artifact is missing normalized actions")

    allow_delete = bool(constraints.get("allow_delete"))
    allow_project_delete = bool(constraints.get("allow_project_delete"))
    for action in actions:
        if not isinstance(action, dict) or action.get("skipped"):
            continue
        if action.get("operation") != "delete":
            continue
        if not allow_delete:
            raise ValueError("Refusing to apply delete action without allow_delete=true in the plan artifact")
        if action.get("resource") == "project" and not allow_project_delete:
            raise ValueError(
                "Refusing to apply project delete action without allow_project_delete=true in the plan artifact"
            )


def run_plan(args: argparse.Namespace) -> None:
    from ado_ops.ado_client import ADOClient
    from ado_ops.executor import build_context, execute_plan, normalize_plan
    from ado_ops.planner import plan_actions

    client = ADOClient()
    context = build_context(
        client,
        project=args.project.strip(),
        source=args.source,
        ids=parse_ids(args.ids),
        recent_days=args.recent_days,
        max_items=args.max_items,
    )
    constraints = _constraints(args)
    raw_plan = plan_actions(args.objective, context, constraints)
    normalized_plan = normalize_plan(
        raw_plan,
        allow_delete=args.allow_delete,
        allow_project_delete=args.allow_project_delete,
        max_actions=args.max_actions,
    )
    dry_run = execute_plan(client, normalized_plan, apply=False, hard_delete=args.hard_delete)
    bundle = {
        "generated_at": datetime.now(UTC).isoformat(),
        "objective": args.objective,
        "context": context,
        "constraints": constraints,
        "raw_plan": raw_plan,
        "normalized_plan": normalized_plan,
        "dry_run": dry_run,
    }
    _write_json(args.out, bundle)
    _write_markdown(args.markdown, _plan_markdown(bundle))
    print(f"Wrote validated plan to {args.out}")
    print(f"Wrote plan summary to {args.markdown}")


def run_apply(args: argparse.Namespace) -> None:
    from ado_ops.ado_client import ADOClient
    from ado_ops.executor import execute_plan

    bundle = _read_json(args.plan)
    _assert_safe_to_apply(bundle)
    hard_delete = bool(bundle.get("constraints", {}).get("hard_delete"))
    apply_results = execute_plan(
        ADOClient(),
        bundle["normalized_plan"],
        apply=True,
        hard_delete=hard_delete,
    )
    payload = {
        "applied_at": datetime.now(UTC).isoformat(),
        "objective": bundle.get("objective", ""),
        "plan_generated_at": bundle.get("generated_at", ""),
        "apply_results": apply_results,
    }
    _write_json(args.out, payload)
    _write_markdown(args.markdown, _apply_markdown(payload))
    print(f"Wrote apply results to {args.out}")
    print(f"Wrote apply summary to {args.markdown}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ADO Control Center agent from automation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Generate and dry-run a validated ADO plan.")
    plan.add_argument("--objective", required=True, help="Natural-language objective for the agent.")
    plan.add_argument("--project", default="", help="Project name used to collect scoped context.")
    plan.add_argument(
        "--source",
        choices=["projects", "project", "iterations", "recent", "ids"],
        default="projects",
        help="Context source to send to the planner.",
    )
    plan.add_argument("--ids", default="", help="Comma, space, or newline separated work item IDs.")
    plan.add_argument("--recent-days", type=int, default=7)
    plan.add_argument("--max-items", type=int, default=30)
    plan.add_argument("--max-actions", type=int, default=10)
    plan.add_argument("--allow-delete", action="store_true")
    plan.add_argument("--allow-project-delete", action="store_true")
    plan.add_argument("--hard-delete", action="store_true")
    plan.add_argument("--out", type=Path, default=Path("artifacts/plan.json"))
    plan.add_argument("--markdown", type=Path, default=Path("artifacts/plan.md"))
    plan.set_defaults(func=run_plan)

    apply = subparsers.add_parser("apply", help="Apply a validated plan artifact.")
    apply.add_argument("--plan", type=Path, required=True, help="Plan JSON produced by the plan command.")
    apply.add_argument("--out", type=Path, default=Path("artifacts/apply.json"))
    apply.add_argument("--markdown", type=Path, default=Path("artifacts/apply.md"))
    apply.set_defaults(func=run_apply)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
