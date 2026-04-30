"""Plan validation and execution for ADO Control Center."""

from __future__ import annotations

from typing import Any

from ado_ops.ado_client import ADOClient
from ado_ops.settings import settings

CREATED_SPRINT_PATH_TOKEN = "{{created_sprint_path}}"

VALID_RESOURCES = {"project", "sprint", "work_item"}
VALID_OPERATIONS = {"list", "read", "create", "update", "delete"}

PROJECT_FIELDS = {"name", "description", "abbreviation", "visibility", "process_name", "source_control_type"}
SPRINT_FIELDS = {"name", "start_date", "finish_date", "parent_path", "add_to_team"}
WORK_ITEM_FIELDS = {
    "System.Title",
    "System.Description",
    "System.State",
    "System.Tags",
    "System.AssignedTo",
    "System.IterationPath",
    "Microsoft.VSTS.Common.Priority",
}
SCALAR_TYPES = (str, int, float, bool, type(None))


def build_context(
    client: ADOClient,
    *,
    project: str = "",
    source: str = "projects",
    ids: list[int] | None = None,
    recent_days: int = 7,
    max_items: int = 30,
) -> dict[str, Any]:
    """Collect bounded context for the LLM."""
    context: dict[str, Any] = {
        "projects": [
            {"id": item.get("id"), "name": item.get("name"), "state": item.get("state")}
            for item in client.list_projects()
        ]
    }
    if not project:
        return context

    context["selected_project"] = project
    if source == "ids":
        context["work_items"] = client.get_work_items(project, ids or [])[:max_items]
    elif source == "recent":
        context["work_items"] = client.recent_work_items(project, days=recent_days, top=max_items)
    elif source == "iterations":
        context["iterations"] = client.list_iterations(project)
    elif source == "project":
        context["project"] = client.get_project(project)
    return context


def normalize_plan(
    plan: dict[str, Any],
    *,
    allow_delete: bool = False,
    allow_project_delete: bool = False,
    max_actions: int = 10,
) -> dict[str, Any]:
    """Validate and normalize an LLM action plan before execution."""
    raw_actions = plan.get("actions", [])
    if not isinstance(raw_actions, list):
        raise ValueError("Plan actions must be a list")

    warnings = list(plan.get("warnings", [])) if isinstance(plan.get("warnings", []), list) else []
    if len(raw_actions) > max_actions:
        warnings.append(f"Truncated actions from {len(raw_actions)} to max_actions={max_actions}")
        raw_actions = raw_actions[:max_actions]

    actions = []
    for index, raw in enumerate(raw_actions, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"Skipped action {index}: action was not an object")
            continue

        resource = str(raw.get("resource", "")).lower().strip()
        operation = str(raw.get("operation", "")).lower().strip()
        action = {
            "resource": resource,
            "operation": operation,
            "project": str(raw.get("project", "")).strip(),
            "target": raw.get("target"),
            "name": str(raw.get("name", "")).strip(),
            "reason": str(raw.get("reason", "")).strip(),
            "fields": {},
            "skipped": False,
            "skip_reason": "",
        }

        if resource not in VALID_RESOURCES:
            action.update(skipped=True, skip_reason=f"Unknown resource: {resource}")
            actions.append(action)
            continue
        if operation not in VALID_OPERATIONS:
            action.update(skipped=True, skip_reason=f"Unknown operation: {operation}")
            actions.append(action)
            continue
        if operation == "delete" and not allow_delete:
            action.update(skipped=True, skip_reason="Delete requires the allow-delete gate")
        if resource == "project" and operation == "delete" and not allow_project_delete:
            action.update(skipped=True, skip_reason="Project delete requires the project-delete gate")

        fields, field_warnings = _sanitize_fields(resource, raw.get("fields", {}))
        warnings.extend(field_warnings)
        action["fields"] = fields

        if resource == "project":
            _normalize_project_action(action, raw)
        elif resource == "sprint":
            _normalize_sprint_action(action, raw)
        elif resource == "work_item":
            _normalize_work_item_action(action, raw)

        actions.append(action)

    return {
        "summary": str(plan.get("summary", "")).strip(),
        "warnings": warnings,
        "actions": actions,
    }


def execute_plan(
    client: ADOClient,
    normalized_plan: dict[str, Any],
    *,
    apply: bool = False,
    hard_delete: bool = False,
) -> dict[str, Any]:
    """Execute or dry-run a normalized plan."""
    execution_context: dict[str, Any] = {}
    results = []
    for action in normalized_plan["actions"]:
        results.append(
            _execute_action(
                client,
                action,
                apply=apply,
                hard_delete=hard_delete,
                execution_context=execution_context,
            )
        )
    return {
        "apply": apply,
        "execution_context": execution_context,
        "results": results,
    }


def _sanitize_fields(resource: str, fields: Any) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if fields is None:
        return {}, []
    if not isinstance(fields, dict):
        return {}, ["Dropped fields because fields was not an object"]

    allowed = {
        "project": PROJECT_FIELDS,
        "sprint": SPRINT_FIELDS,
        "work_item": WORK_ITEM_FIELDS,
    }.get(resource, set())
    clean = {}
    for field, value in fields.items():
        if field not in allowed:
            warnings.append(f"Dropped disallowed {resource} field: {field}")
            continue
        if not isinstance(value, SCALAR_TYPES):
            warnings.append(f"Dropped non-scalar {resource} field: {field}")
            continue
        clean[field] = value
    return clean, warnings


def _normalize_project_action(action: dict[str, Any], raw: dict[str, Any]) -> None:
    if action["operation"] in {"read", "update", "delete"} and not action["target"]:
        action.update(skipped=True, skip_reason="Project target is required")
    if action["operation"] == "create":
        if not action["name"]:
            action["name"] = str(action["fields"].get("name", "")).strip()
        if not action["name"]:
            action.update(skipped=True, skip_reason="Project create requires name")
        action["fields"].setdefault("description", str(raw.get("description", "")).strip())
        action["fields"].setdefault("process_name", str(raw.get("process_name", "")).strip())
        action["fields"].setdefault("visibility", str(raw.get("visibility", "")).strip())


def _normalize_sprint_action(action: dict[str, Any], raw: dict[str, Any]) -> None:
    if action["operation"] in {"list", "create", "update", "delete"} and not action["project"]:
        action.update(skipped=True, skip_reason="Sprint action requires project")
    if action["operation"] == "create":
        if not action["name"]:
            action["name"] = str(action["fields"].get("name", "")).strip()
        if not action["name"]:
            action.update(skipped=True, skip_reason="Sprint create requires name")
        for key in ("start_date", "finish_date", "parent_path", "add_to_team"):
            if key in raw and key not in action["fields"]:
                action["fields"][key] = raw[key]
    if action["operation"] in {"update", "delete", "read"} and not action["target"]:
        action.update(skipped=True, skip_reason="Sprint target path/name is required")


def _normalize_work_item_action(action: dict[str, Any], raw: dict[str, Any]) -> None:
    if action["operation"] in {"list", "read", "create", "update", "delete"} and not action["project"]:
        action.update(skipped=True, skip_reason="Work item action requires project")
    if action["operation"] == "create":
        action["work_item_type"] = str(raw.get("work_item_type", "Task")).strip() or "Task"
        if action["name"] and "System.Title" not in action["fields"]:
            action["fields"]["System.Title"] = action["name"]
        action["fields"]["System.AssignedTo"] = settings.default_work_item_assignee
        if "System.Title" not in action["fields"]:
            action.update(skipped=True, skip_reason="Work item create requires System.Title or name")
    if action["operation"] in {"read", "update", "delete"}:
        try:
            action["target"] = int(action["target"])
        except (TypeError, ValueError):
            action.update(skipped=True, skip_reason="Work item target must be an integer ID")
    if action["operation"] == "update" and not action["fields"]:
        action.update(skipped=True, skip_reason="Work item update requires allowed fields")


def _execute_action(
    client: ADOClient,
    action: dict[str, Any],
    *,
    apply: bool,
    hard_delete: bool,
    execution_context: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "resource": action["resource"],
        "operation": action["operation"],
        "project": action.get("project", ""),
        "target": action.get("target"),
        "status": "skipped" if action.get("skipped") else "dry_run",
        "reason": action.get("reason", ""),
    }
    if action.get("skipped"):
        result["message"] = action.get("skip_reason", "Skipped by validator")
        return result
    if not apply:
        result["planned_action"] = action
        return result

    resource = action["resource"]
    if resource == "project":
        return _execute_project(client, action, result)
    if resource == "sprint":
        return _execute_sprint(client, action, result, execution_context)
    if resource == "work_item":
        return _execute_work_item(client, action, result, hard_delete, execution_context)
    result["status"] = "skipped"
    result["message"] = "Unhandled action"
    return result


def _execute_project(client: ADOClient, action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    operation = action["operation"]
    if operation == "list":
        result["data"] = client.list_projects()
    elif operation == "read":
        result["data"] = client.get_project(str(action["target"]))
    elif operation == "create":
        result["data"] = client.create_project(
            action["name"],
            description=str(action["fields"].get("description", "")),
            process_name=str(action["fields"].get("process_name", "")),
            visibility=str(action["fields"].get("visibility", "")),
            source_control_type=str(action["fields"].get("source_control_type", "Git") or "Git"),
        )
    elif operation == "update":
        result["data"] = client.update_project(str(action["target"]), action["fields"])
    elif operation == "delete":
        result["data"] = client.delete_project(str(action["target"]))
    result["status"] = "applied"
    return result


def _execute_sprint(
    client: ADOClient,
    action: dict[str, Any],
    result: dict[str, Any],
    execution_context: dict[str, Any],
) -> dict[str, Any]:
    project = action["project"]
    operation = action["operation"]
    if operation == "list":
        result["data"] = client.list_iterations(project)
    elif operation == "create":
        created = client.create_iteration(
            project,
            action["name"],
            start_date=str(action["fields"].get("start_date", "")),
            finish_date=str(action["fields"].get("finish_date", "")),
            parent_path=str(action["fields"].get("parent_path", "")),
        )
        sprint_path = client.work_item_iteration_path(
            project,
            created,
            parent_path=str(action["fields"].get("parent_path", "")),
        )
        execution_context["created_sprint_path"] = sprint_path
        result["data"] = created
        result["iteration_path"] = sprint_path
        if action["fields"].get("add_to_team", True):
            iteration_id = created.get("identifier") or created.get("id")
            if iteration_id:
                result["team_schedule"] = client.add_iteration_to_team(project, iteration_id)
    elif operation == "update":
        result["data"] = client.update_iteration(project, str(action["target"]), action["fields"])
    elif operation == "delete":
        result["data"] = client.delete_iteration(project, str(action["target"]))
    result["status"] = "applied"
    return result


def _execute_work_item(
    client: ADOClient,
    action: dict[str, Any],
    result: dict[str, Any],
    hard_delete: bool,
    execution_context: dict[str, Any],
) -> dict[str, Any]:
    project = action["project"]
    operation = action["operation"]
    if operation == "list":
        result["data"] = client.recent_work_items(project)
    elif operation == "read":
        result["data"] = client.get_work_item(project, int(action["target"]))
    elif operation == "create":
        fields = _resolve_created_sprint_path(action["fields"], execution_context)
        if fields.get("System.IterationPath") == CREATED_SPRINT_PATH_TOKEN:
            result["status"] = "skipped"
            result["message"] = "System.IterationPath references a sprint that was not created earlier"
            return result
        result["data"] = client.create_work_item(project, action["work_item_type"], fields)
    elif operation == "update":
        result["data"] = client.update_work_item(project, int(action["target"]), action["fields"])
    elif operation == "delete":
        result["data"] = client.delete_work_item(project, int(action["target"]), destroy=hard_delete)
    result["status"] = "applied"
    return result


def _resolve_created_sprint_path(fields: dict[str, Any], execution_context: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(fields)
    if resolved.get("System.IterationPath") == CREATED_SPRINT_PATH_TOKEN:
        sprint_path = execution_context.get("created_sprint_path")
        if sprint_path:
            resolved["System.IterationPath"] = sprint_path
    return resolved
