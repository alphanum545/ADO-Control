"""LLM planner for Azure DevOps operations."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ado_ops.settings import settings

PLAN_SCHEMA = {
    "summary": "Short explanation of the intended changes.",
    "actions": [
        {
            "resource": "project | sprint | work_item",
            "operation": "list | read | create | update | delete",
            "project": "Project name for sprint/work_item actions.",
            "target": "Project name/id, sprint path/name, or work item id when applicable.",
            "name": "New project, sprint, or work item name when applicable.",
            "work_item_type": "Task | Bug | User Story | Epic | Feature, only for work_item create.",
            "fields": "Object of fields to create/update.",
            "reason": "Why this action should be taken.",
        }
    ],
    "warnings": ["Assumptions, blockers, or permission concerns."],
}

SYSTEM_PROMPT = """You are an Azure DevOps operations planner.
Return only valid JSON. Do not wrap the JSON in Markdown.

You can plan CRUD operations for these resources:
- project
- sprint (Azure DevOps iteration)
- work_item

Critical rules:
- Produce the smallest safe plan that satisfies the objective.
- Delete actions are allowed in the plan only when the user explicitly asks to delete.
- Project creation is asynchronous in Azure DevOps. Do not create sprints or work items
  inside a newly created project in the same plan.
- For work items in a sprint created earlier in the same plan, set
  fields.System.IterationPath to "{{created_sprint_path}}".
- Newly created work items are assigned by the executor, so do not rely on the model
  for System.AssignedTo.
- Use Azure DevOps field reference names such as System.Title, System.Description,
  System.State, System.Tags, System.IterationPath, and Microsoft.VSTS.Common.Priority.
- If an operation is uncertain or unsafe, put it in warnings instead of actions.
"""


def _client() -> OpenAI:
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=120,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def call_llm(prompt: str) -> str:
    response = _client().chat.completions.create(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def build_prompt(objective: str, context: dict[str, Any], constraints: dict[str, Any]) -> str:
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "objective": objective,
        "context": context,
        "constraints": constraints,
        "required_json_schema": PLAN_SCHEMA,
    }
    return json.dumps(payload, indent=2, default=str)


def extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response did not contain a JSON object")
        raw = raw[start : end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("LLM plan must be a JSON object")
    return parsed


def plan_actions(objective: str, context: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    prompt = build_prompt(objective, context, constraints)
    return extract_json(call_llm(prompt))

