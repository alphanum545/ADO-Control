"""Tests for the GitHub Actions command-line runner helpers."""

from ado_ops.actions_runner import _assert_safe_to_apply, parse_ids


def test_parse_ids_accepts_commas_spaces_and_newlines():
    assert parse_ids("163, 164\n165") == [163, 164, 165]


def test_apply_guard_rejects_delete_without_gate():
    bundle = {
        "constraints": {"allow_delete": False, "allow_project_delete": False},
        "normalized_plan": {
            "actions": [
                {
                    "resource": "work_item",
                    "operation": "delete",
                    "project": "Alpha",
                    "target": 163,
                    "skipped": False,
                }
            ]
        },
    }

    try:
        _assert_safe_to_apply(bundle)
    except ValueError as exc:
        assert "allow_delete=true" in str(exc)
    else:
        raise AssertionError("Expected delete apply guard to reject missing allow_delete gate")
