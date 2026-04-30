"""Azure DevOps REST client used by the control center."""

from __future__ import annotations

import time
from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from ado_ops.settings import settings

API_VERSION = "7.1"
TIMEOUT_SECONDS = 30
MAX_RETRIES = 3


@dataclass(frozen=True)
class ADOConfig:
    """Runtime ADO connection details."""

    org: str
    pat: str

    @classmethod
    def from_settings(cls) -> "ADOConfig":
        return cls(org=settings.ado_org, pat=settings.ado_pat)


class ADOClient:
    """Small Azure DevOps REST wrapper for projects, iterations, and work items."""

    def __init__(self, config: ADOConfig | None = None) -> None:
        self.config = config or ADOConfig.from_settings()
        self.base_url = f"https://dev.azure.com/{self.config.org}"

    def _auth_header(self) -> dict[str, str]:
        token = b64encode(f":{self.config.pat}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", TIMEOUT_SECONDS)
        kwargs.setdefault("headers", self._auth_header())
        params = kwargs.setdefault("params", {})
        params.setdefault("api-version", API_VERSION)

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                time.sleep(2**attempt)
        if last_error:
            raise last_error
        raise RuntimeError(f"ADO request failed: {method} {url}")

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", url, params=params or {}).json()

    def _post(
        self,
        url: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        *,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        headers = {**self._auth_header(), "Content-Type": content_type}
        return self._request("POST", url, headers=headers, json=body, params=params or {}).json()

    def _patch(
        self,
        url: str,
        body: dict[str, Any] | list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        *,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        headers = {**self._auth_header(), "Content-Type": content_type}
        return self._request("PATCH", url, headers=headers, json=body, params=params or {}).json()

    def _delete(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request("DELETE", url, params=params or {})
        if not response.content:
            return {}
        return response.json()

    def _project_url(self, project: str) -> str:
        safe_project = requests.utils.quote(project, safe="")
        return f"{self.base_url}/{safe_project}"

    # Projects

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._get(f"{self.base_url}/_apis/projects", {"$top": 500})
        return sorted(data.get("value", []), key=lambda item: item.get("name", ""))

    def get_project(self, project_name_or_id: str, *, include_capabilities: bool = True) -> dict[str, Any]:
        safe_project = requests.utils.quote(project_name_or_id, safe="")
        return self._get(
            f"{self.base_url}/_apis/projects/{safe_project}",
            {"includeCapabilities": str(include_capabilities).lower()},
        )

    def list_processes(self) -> list[dict[str, Any]]:
        data = self._get(f"{self.base_url}/_apis/process/processes")
        return data.get("value", [])

    def resolve_process_template_id(self, process_name: str = "") -> str:
        preferred = process_name or settings.default_project_process
        processes = self.list_processes()
        for process in processes:
            if process.get("name", "").lower() == preferred.lower():
                return process["id"]
        for fallback in ("Agile", "Scrum", "Basic", "CMMI"):
            for process in processes:
                if process.get("name", "").lower() == fallback.lower():
                    return process["id"]
        if processes:
            return processes[0]["id"]
        raise RuntimeError("No Azure DevOps process templates were available")

    def create_project(
        self,
        name: str,
        *,
        description: str = "",
        process_name: str = "",
        visibility: str = "",
        source_control_type: str = "Git",
    ) -> dict[str, Any]:
        template_id = self.resolve_process_template_id(process_name)
        body = {
            "name": name,
            "description": description,
            "visibility": visibility or settings.default_project_visibility,
            "capabilities": {
                "versioncontrol": {"sourceControlType": source_control_type},
                "processTemplate": {"templateTypeId": template_id},
            },
        }
        return self._post(f"{self.base_url}/_apis/projects", body)

    def update_project(self, project_name_or_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        project = self.get_project(project_name_or_id)
        allowed = {"name", "description", "abbreviation", "visibility"}
        body = {key: value for key, value in fields.items() if key in allowed and value not in ("", None)}
        if not body:
            raise ValueError("No allowed project fields supplied")
        return self._patch(f"{self.base_url}/_apis/projects/{project['id']}", body)

    def delete_project(self, project_name_or_id: str) -> dict[str, Any]:
        project = self.get_project(project_name_or_id)
        return self._delete(f"{self.base_url}/_apis/projects/{project['id']}")

    # Sprints / iterations

    def list_iterations(self, project: str, *, depth: int = 4) -> dict[str, Any]:
        return self._get(
            f"{self._project_url(project)}/_apis/wit/classificationnodes/Iterations",
            {"$depth": depth},
        )

    def create_iteration(
        self,
        project: str,
        name: str,
        *,
        start_date: str = "",
        finish_date: str = "",
        parent_path: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        attributes = {}
        if start_date:
            attributes["startDate"] = _format_iteration_date(start_date)
        if finish_date:
            attributes["finishDate"] = _format_iteration_date(finish_date)
        if attributes:
            body["attributes"] = attributes
        relative_parent = self._relative_iteration_path(project, parent_path)
        suffix = f"/{requests.utils.quote(relative_parent.replace('\\', '/'), safe='/')}" if relative_parent else ""
        created = self._post(f"{self._project_url(project)}/_apis/wit/classificationnodes/Iterations{suffix}", body)
        if attributes and not _iteration_has_dates(created, attributes):
            created_path = self.work_item_iteration_path(project, created, parent_path=parent_path)
            created = self.update_iteration(
                project,
                created_path,
                {
                    "start_date": attributes.get("startDate", ""),
                    "finish_date": attributes.get("finishDate", ""),
                },
            )
        return created

    def update_iteration(self, project: str, path: str, fields: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if fields.get("name"):
            body["name"] = fields["name"]
        attributes = {}
        if fields.get("start_date"):
            attributes["startDate"] = _format_iteration_date(str(fields["start_date"]))
        if fields.get("finish_date"):
            attributes["finishDate"] = _format_iteration_date(str(fields["finish_date"]))
        if attributes:
            body["attributes"] = attributes
        if not body:
            raise ValueError("No sprint fields supplied")
        relative_path = self._relative_iteration_path(project, path)
        encoded_path = requests.utils.quote(relative_path.replace("\\", "/"), safe="/")
        return self._post(f"{self._project_url(project)}/_apis/wit/classificationnodes/Iterations/{encoded_path}", body)

    def delete_iteration(self, project: str, path: str) -> dict[str, Any]:
        relative_path = self._relative_iteration_path(project, path)
        encoded_path = requests.utils.quote(relative_path.replace("\\", "/"), safe="/")
        return self._delete(f"{self._project_url(project)}/_apis/wit/classificationnodes/Iterations/{encoded_path}")

    def add_iteration_to_team(self, project: str, iteration_id: str) -> dict[str, Any]:
        return self._post(f"{self._project_url(project)}/_apis/work/teamsettings/iterations", {"id": iteration_id})

    def _relative_iteration_path(self, project: str, path: str) -> str:
        parts = [part for part in path.replace("/", "\\").strip().strip("\\").split("\\") if part]
        if parts and parts[0].lower() == project.lower():
            parts = parts[1:]
        while parts and parts[0].lower() in {"iteration", "iterations"}:
            parts = parts[1:]
        return "\\".join(parts)

    def work_item_iteration_path(self, project: str, iteration_result: dict[str, Any], parent_path: str = "") -> str:
        raw_path = iteration_result.get("path", "").strip("\\")
        if raw_path:
            return raw_path
        name = iteration_result.get("name", "")
        parent = parent_path.strip().strip("\\/")
        if not parent:
            return f"{project}\\{name}"
        if parent.lower().startswith(f"{project.lower()}\\"):
            return f"{parent}\\{name}"
        return f"{project}\\{parent}\\{name}"

    # Work items

    def get_work_item(self, project: str, item_id: int) -> dict[str, Any]:
        items = self.get_work_items(project, [item_id])
        if not items:
            raise ValueError(f"Work item not found: {item_id}")
        return items[0]

    def get_work_items(self, project: str, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        results: list[dict[str, Any]] = []
        fields = (
            "System.Id,System.Title,System.State,System.WorkItemType,System.AssignedTo,"
            "System.CreatedDate,System.ChangedDate,System.IterationPath,System.Tags"
        )
        for index in range(0, len(ids), 200):
            batch = ids[index : index + 200]
            id_str = ",".join(str(item_id) for item_id in batch)
            data = self._get(f"{self._project_url(project)}/_apis/wit/workitems", {"ids": id_str, "fields": fields})
            for item in data.get("value", []):
                results.append(self._flatten_work_item(item))
        return results

    def query_work_items(self, project: str, wiql: str) -> list[dict[str, Any]]:
        data = self._post(f"{self._project_url(project)}/_apis/wit/wiql", {"query": wiql})
        ids = [item["id"] for item in data.get("workItems", [])]
        return self.get_work_items(project, ids)

    def recent_work_items(self, project: str, *, days: int = 7, top: int = 50) -> list[dict[str, Any]]:
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        wiql = (
            "SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType], "
            "[System.AssignedTo], [System.ChangedDate] "
            f"FROM WorkItems WHERE [System.TeamProject] = '{project}' "
            f"AND [System.ChangedDate] >= '{since}' "
            "ORDER BY [System.ChangedDate] DESC"
        )
        return self.query_work_items(project, wiql)[:top]

    def create_work_item(self, project: str, work_item_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        safe_type = requests.utils.quote(work_item_type, safe="")
        patch = self._field_patch(fields, op="add")
        return self._patch(
            f"{self._project_url(project)}/_apis/wit/workitems/${safe_type}",
            patch,
            content_type="application/json-patch+json",
        )

    def update_work_item(self, project: str, item_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        patch = self._field_patch(fields, op="add")
        return self._patch(
            f"{self._project_url(project)}/_apis/wit/workitems/{item_id}",
            patch,
            content_type="application/json-patch+json",
        )

    def delete_work_item(self, project: str, item_id: int, *, destroy: bool = False) -> dict[str, Any]:
        return self._delete(
            f"{self._project_url(project)}/_apis/wit/workitems/{item_id}",
            {"destroy": str(destroy).lower()},
        )

    def _field_patch(self, fields: dict[str, Any], *, op: str) -> list[dict[str, Any]]:
        return [
            {"op": op, "path": f"/fields/{field}", "value": value}
            for field, value in fields.items()
            if value not in (None, "")
        ]

    def _flatten_work_item(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = item.get("fields", {})
        assigned_to = fields.get("System.AssignedTo") or {}
        if isinstance(assigned_to, dict):
            assignee = assigned_to.get("displayName") or assigned_to.get("uniqueName") or ""
        else:
            assignee = str(assigned_to)
        return {
            "id": item.get("id"),
            "rev": item.get("rev"),
            "title": fields.get("System.Title", ""),
            "state": fields.get("System.State", ""),
            "type": fields.get("System.WorkItemType", ""),
            "assigned_to": assignee,
            "changed_date": fields.get("System.ChangedDate", ""),
            "created_date": fields.get("System.CreatedDate", ""),
            "iteration_path": fields.get("System.IterationPath", ""),
            "tags": fields.get("System.Tags", ""),
            "url": item.get("url"),
        }


def _format_iteration_date(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _iteration_has_dates(iteration: dict[str, Any], expected: dict[str, str]) -> bool:
    attributes = iteration.get("attributes")
    if not isinstance(attributes, dict):
        return False
    for key, expected_value in expected.items():
        if expected_value and not attributes.get(key):
            return False
    return True
