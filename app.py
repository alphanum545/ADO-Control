"""Streamlit web UI for ADO Control Center."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from ado_ops.ado_client import ADOClient
from ado_ops.executor import build_context, execute_plan, normalize_plan
from ado_ops.planner import plan_actions
from ado_ops.settings import settings

st.set_page_config(page_title="ADO Control Center", layout="wide")


@st.cache_resource(show_spinner=False)
def get_client() -> ADOClient:
    return ADOClient()


def parse_ids(raw: str) -> list[int]:
    ids = []
    for token in raw.split(","):
        token = token.strip()
        if token:
            ids.append(int(token))
    return ids


def render_actions(actions: list[dict[str, Any]]) -> None:
    rows = []
    for index, action in enumerate(actions, start=1):
        rows.append(
            {
                "#": index,
                "resource": action.get("resource"),
                "operation": action.get("operation"),
                "project": action.get("project"),
                "target": action.get("target"),
                "name": action.get("name"),
                "skipped": action.get("skipped"),
                "reason": action.get("reason"),
                "skip_reason": action.get("skip_reason"),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_results(results: dict[str, Any]) -> None:
    rows = []
    for index, result in enumerate(results.get("results", []), start=1):
        data = result.get("data") or {}
        rows.append(
            {
                "#": index,
                "resource": result.get("resource"),
                "operation": result.get("operation"),
                "status": result.get("status"),
                "project": result.get("project"),
                "target": result.get("target"),
                "id": data.get("id") if isinstance(data, dict) else "",
                "message": result.get("message", ""),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    with st.expander("Raw execution JSON"):
        st.json(results)


st.title("ADO Control Center")
st.caption("Plan-first Azure DevOps operations with LLM reasoning and explicit apply gates.")

client = get_client()

with st.sidebar:
    st.header("Connection")
    st.write(f"Organization: `{settings.ado_org}`")
    st.write(f"Model: `{settings.llm_model}`")
    st.write(f"Default assignee: `{settings.default_work_item_assignee}`")

    st.header("Context")
    source = st.selectbox(
        "Context source",
        ["projects", "project", "iterations", "recent", "ids"],
        help="The bounded data sent to the LLM before it plans actions.",
    )
    project = st.text_input("Project", value="", placeholder="Alpha")
    ids_raw = st.text_input("Work item IDs", value="", placeholder="123,124")
    recent_days = st.number_input("Recent days", min_value=1, max_value=90, value=7)
    max_items = st.number_input("Max work items", min_value=1, max_value=200, value=30)
    max_actions = st.number_input("Max actions", min_value=1, max_value=25, value=10)

    st.header("Apply Gates")
    allow_delete = st.checkbox("Allow deletes in plan", value=False)
    allow_project_delete = st.checkbox("Allow project delete", value=False)
    hard_delete = st.checkbox("Hard-delete work items", value=False)

objective = st.text_area(
    "Objective",
    height=140,
    placeholder=(
        "Create a sprint named AI Test Sprint from 2026-05-01 to 2026-05-15 "
        "and create two tasks in it for the stale work item."
    ),
)

col_generate, col_clear = st.columns([1, 1])
with col_generate:
    generate_clicked = st.button("Generate Plan", type="primary", use_container_width=True)
with col_clear:
    if st.button("Clear", use_container_width=True):
        st.session_state.pop("plan_bundle", None)
        st.session_state.pop("apply_results", None)

if generate_clicked:
    if not objective.strip():
        st.error("Enter an objective first.")
        st.stop()

    with st.spinner("Collecting ADO context and asking the LLM for a plan..."):
        context = build_context(
            client,
            project=project.strip(),
            source=source,
            ids=parse_ids(ids_raw),
            recent_days=int(recent_days),
            max_items=int(max_items),
        )
        constraints = {
            "max_actions": int(max_actions),
            "allow_delete": allow_delete,
            "allow_project_delete": allow_project_delete,
            "hard_delete": hard_delete,
            "default_work_item_assignee": settings.default_work_item_assignee,
            "notes": [
                "Dry-run the plan first.",
                "Apply requires a separate explicit UI action.",
            ],
        }
        raw_plan = plan_actions(objective, context, constraints)
        normalized_plan = normalize_plan(
            raw_plan,
            allow_delete=allow_delete,
            allow_project_delete=allow_project_delete,
            max_actions=int(max_actions),
        )
        dry_run = execute_plan(client, normalized_plan, apply=False, hard_delete=hard_delete)
        st.session_state["plan_bundle"] = {
            "objective": objective,
            "context": context,
            "constraints": constraints,
            "raw_plan": raw_plan,
            "normalized_plan": normalized_plan,
            "dry_run": dry_run,
        }
        st.session_state.pop("apply_results", None)

bundle = st.session_state.get("plan_bundle")
if bundle:
    st.subheader("Validated Plan")
    if bundle["normalized_plan"].get("summary"):
        st.write(bundle["normalized_plan"]["summary"])
    if bundle["normalized_plan"].get("warnings"):
        st.warning("\n".join(f"- {warning}" for warning in bundle["normalized_plan"]["warnings"]))
    render_actions(bundle["normalized_plan"]["actions"])

    with st.expander("Raw LLM plan"):
        st.json(bundle["raw_plan"])
    with st.expander("Context sent to the LLM"):
        st.json(bundle["context"])

    st.subheader("Dry Run")
    render_results(bundle["dry_run"])

    st.subheader("Apply")
    st.write("Type `APPLY` to execute the validated plan against Azure DevOps.")
    confirm = st.text_input("Confirmation", value="", placeholder="APPLY")
    apply_clicked = st.button("Apply Validated Plan", disabled=confirm != "APPLY", type="primary")

    if apply_clicked:
        with st.spinner("Applying validated plan to Azure DevOps..."):
            st.session_state["apply_results"] = execute_plan(
                client,
                bundle["normalized_plan"],
                apply=True,
                hard_delete=bundle["constraints"]["hard_delete"],
            )

if st.session_state.get("apply_results"):
    st.subheader("Apply Results")
    render_results(st.session_state["apply_results"])

with st.expander("Example Objectives"):
    examples = [
        "List all projects and summarize which one should be used for testing.",
        "Create a private project named AI Ops Sandbox using the Agile process.",
        "Create a sprint named AI Test Sprint from 2026-05-01 to 2026-05-15 in project Alpha.",
        (
            "Create a sprint named AI Test Sprint in Alpha, then create two Task work items in that sprint "
            "for the stale work item context."
        ),
        "Update work item 163 in Alpha by adding the tag AI-Test.",
        "Delete work item 999 in Alpha because it is a throwaway AI test item.",
    ]
    st.code("\n\n".join(examples))

with st.expander("Current Plan JSON"):
    st.code(json.dumps(bundle or {}, indent=2, default=str), language="json")
