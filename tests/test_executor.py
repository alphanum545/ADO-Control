"""Tests for ADO operation plan validation."""

import os

os.environ.setdefault("ADO_ORG", "test-org")
os.environ.setdefault("ADO_PAT", "test-pat")
os.environ.setdefault("LLM_API_KEY", "test-key")

from ado_ops.executor import normalize_plan
from ado_ops.settings import settings


def test_work_item_create_forces_default_assignee():
    plan = {
        "summary": "Create a task.",
        "actions": [
            {
                "resource": "work_item",
                "operation": "create",
                "project": "Alpha",
                "work_item_type": "Task",
                "fields": {
                    "System.Title": "Follow up",
                    "System.AssignedTo": "someone@example.com",
                },
            }
        ],
    }

    normalized = normalize_plan(plan)

    fields = normalized["actions"][0]["fields"]
    assert fields["System.AssignedTo"] == settings.default_work_item_assignee


def test_project_delete_requires_both_delete_gates():
    plan = {
        "summary": "Delete a project.",
        "actions": [
            {
                "resource": "project",
                "operation": "delete",
                "target": "Alpha",
            }
        ],
    }

    normalized = normalize_plan(plan, allow_delete=True, allow_project_delete=False)

    action = normalized["actions"][0]
    assert action["skipped"] is True
    assert action["skip_reason"] == "Project delete requires the project-delete gate"


def test_sprint_create_accepts_dates():
    plan = {
        "summary": "Create sprint.",
        "actions": [
            {
                "resource": "sprint",
                "operation": "create",
                "project": "Alpha",
                "name": "AI Test Sprint",
                "start_date": "2026-05-01",
                "finish_date": "2026-05-15",
            }
        ],
    }

    normalized = normalize_plan(plan)

    action = normalized["actions"][0]
    assert action["skipped"] is False
    assert action["fields"]["start_date"] == "2026-05-01"
    assert action["fields"]["finish_date"] == "2026-05-15"


def test_sprint_create_accepts_camel_case_date_fields():
    plan = {
        "summary": "Create sprint.",
        "actions": [
            {
                "resource": "sprint",
                "operation": "create",
                "project": "Alpha",
                "name": "AI Test Sprint",
                "fields": {
                    "startDate": "2026-05-01",
                    "finishDate": "2026-05-15",
                },
            }
        ],
    }

    normalized = normalize_plan(plan)

    action = normalized["actions"][0]
    assert action["skipped"] is False
    assert action["fields"]["start_date"] == "2026-05-01"
    assert action["fields"]["finish_date"] == "2026-05-15"
    assert "Dropped disallowed sprint field: startDate" not in normalized["warnings"]
    assert "Dropped disallowed sprint field: finishDate" not in normalized["warnings"]
