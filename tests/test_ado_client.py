"""Tests for Azure DevOps REST client request shaping."""

import os

os.environ.setdefault("ADO_ORG", "test-org")
os.environ.setdefault("ADO_PAT", "test-pat")
os.environ.setdefault("LLM_API_KEY", "test-key")

from ado_ops.ado_client import _format_iteration_date


def test_format_iteration_date_converts_date_only_to_utc_datetime():
    assert _format_iteration_date("2026-05-01") == "2026-05-01T00:00:00Z"


def test_format_iteration_date_preserves_datetime_as_utc():
    assert _format_iteration_date("2026-05-01T05:30:00+05:30") == "2026-05-01T00:00:00Z"
