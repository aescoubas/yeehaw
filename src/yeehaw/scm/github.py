"""GitHub adapter for roadmap pull-request publication workflows."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from yeehaw.scm.base import SCMAdapterError
from yeehaw.scm.models import (
    RoadmapPRPublication,
    RoadmapPRPublishRequest,
    RoadmapPRPublishResult,
    SCMAlert,
    SCMEvent,
)


@dataclass(frozen=True)
class GitHubSCMAdapter:
    """GitHub adapter that creates or updates one roadmap pull request."""

    owner: str
    repo: str
    token: str
    enabled: bool = False
    api_base_url: str = "https://api.github.com"
    timeout_sec: float = 15.0
    user_agent: str = "yeehaw"

    def publish_roadmap_pull_request(
        self,
        publish_request: RoadmapPRPublishRequest,
    ) -> RoadmapPRPublishResult:
        """Create/update a roadmap PR and return structured event + alert payloads."""
        if not self.enabled or not publish_request.enabled:
            return RoadmapPRPublishResult(
                provider="github",
                action="skipped",
                events=(
                    SCMEvent(
                        kind="roadmap_pr_publish_skipped",
                        message="GitHub PR automation disabled",
                    ),
                ),
            )

        try:
            title = publish_request.title or self._default_title(publish_request)
            body = self._build_pr_body(publish_request)
            existing = self._find_open_pull_request(
                integration_branch=publish_request.integration_branch,
                base_branch=publish_request.base_branch,
            )

            if existing is None:
                created = self._request_json(
                    "POST",
                    self._repo_path("/pulls"),
                    payload={
                        "title": title,
                        "head": publish_request.integration_branch,
                        "base": publish_request.base_branch,
                        "body": body,
                    },
                )
                publication = self._build_publication(created)
                return RoadmapPRPublishResult(
                    provider="github",
                    action="created",
                    pull_request=publication,
                    events=(
                        SCMEvent(
                            kind="roadmap_pr_created",
                            message=f"Created roadmap PR #{publication.number}",
                        ),
                    ),
                )

            existing_number = self._require_int(existing, key="number")
            updated = self._request_json(
                "PATCH",
                self._repo_path(f"/pulls/{existing_number}"),
                payload={
                    "title": title,
                    "base": publish_request.base_branch,
                    "body": body,
                },
            )
            publication = self._build_publication(updated)
            return RoadmapPRPublishResult(
                provider="github",
                action="updated",
                pull_request=publication,
                events=(
                    SCMEvent(
                        kind="roadmap_pr_updated",
                        message=f"Updated roadmap PR #{publication.number}",
                    ),
                ),
            )
        except SCMAdapterError as exc:
            message = str(exc)
            return RoadmapPRPublishResult(
                provider="github",
                action="failed",
                error=message,
                events=(SCMEvent(kind="roadmap_pr_publish_failed", message=message),),
                alerts=(SCMAlert(severity="warn", message=message),),
            )

    def _repo_path(self, suffix: str) -> str:
        """Return a repository-scoped API path."""
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    def _find_open_pull_request(
        self,
        *,
        integration_branch: str,
        base_branch: str,
    ) -> dict[str, Any] | None:
        query = urllib_parse.urlencode(
            {
                "state": "open",
                "head": f"{self.owner}:{integration_branch}",
                "base": base_branch,
            }
        )
        response = self._request_json("GET", f"{self._repo_path('/pulls')}?{query}")
        if not isinstance(response, list):
            raise SCMAdapterError("GitHub API returned invalid pull request list payload")
        for item in response:
            if isinstance(item, dict):
                return item
        return None

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_obj = urllib_request.Request(
            url=f"{self.api_base_url.rstrip('/')}{path}",
            data=body,
            method=method,
        )
        request_obj.add_header("Accept", "application/vnd.github+json")
        request_obj.add_header("Authorization", f"Bearer {self.token}")
        request_obj.add_header("User-Agent", self.user_agent)
        request_obj.add_header("X-GitHub-Api-Version", "2022-11-28")
        if payload is not None:
            request_obj.add_header("Content-Type", "application/json")

        try:
            with urllib_request.urlopen(request_obj, timeout=self.timeout_sec) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = self._read_http_error(exc)
            raise SCMAdapterError(
                f"GitHub API {method} {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except urllib_error.URLError as exc:
            raise SCMAdapterError(f"GitHub API {method} {path} failed: {exc.reason}") from exc

        if not raw_body.strip():
            return {}
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise SCMAdapterError(
                f"GitHub API {method} {path} returned invalid JSON: {raw_body!r}"
            ) from exc

    @staticmethod
    def _read_http_error(exc: urllib_error.HTTPError) -> str:
        """Extract best-effort message from GitHub HTTP error payload."""
        body = exc.read().decode("utf-8").strip()
        if not body:
            return "empty response body"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message:
                return message
        return body

    def _default_title(self, publish_request: RoadmapPRPublishRequest) -> str:
        """Return deterministic title when callers do not provide one."""
        return (
            f"Roadmap {publish_request.roadmap_id}: "
            f"{publish_request.integration_branch} -> {publish_request.base_branch}"
        )

    def _build_pr_body(self, publish_request: RoadmapPRPublishRequest) -> str:
        """Render task/phase summaries into a compact markdown PR body."""
        summary = publish_request.summary
        lines: list[str] = [f"## Roadmap #{publish_request.roadmap_id}", ""]
        lines.append(f"- Base branch: `{publish_request.base_branch}`")
        lines.append(f"- Integration branch: `{publish_request.integration_branch}`")
        if summary is not None:
            lines.append(f"- Commits ahead: {summary.commits_ahead}")
            lines.append(f"- Head SHA: `{summary.head_sha}`")

        if summary is not None and summary.commit_subjects:
            lines.append("")
            lines.append("### Commit Summary")
            for subject in summary.commit_subjects:
                lines.append(f"- {subject}")

        if summary is not None and summary.changed_files:
            lines.append("")
            lines.append("### Changed Files")
            for changed_file in summary.changed_files:
                lines.append(f"- `{changed_file}`")

        if publish_request.phase_summaries:
            lines.append("")
            lines.append("### Phase and Task Summary")
            for phase in publish_request.phase_summaries:
                lines.append(
                    f"- Phase {phase.phase_number} [{phase.status}]: {phase.title}"
                )
                for task in phase.tasks:
                    task_line = f"  - Task {task.task_number} [{task.status}]: {task.title}"
                    if task.summary:
                        task_line = f"{task_line} - {task.summary}"
                    lines.append(task_line)

        return "\n".join(lines).strip()

    def _build_publication(self, payload: Any) -> RoadmapPRPublication:
        """Convert GitHub API response into typed pull request metadata."""
        if not isinstance(payload, dict):
            raise SCMAdapterError("GitHub API returned invalid pull request payload")
        number = self._require_int(payload, key="number")
        html_url = self._require_str(payload, key="html_url")
        title = self._require_str(payload, key="title")
        body_raw = payload.get("body")
        body = body_raw if isinstance(body_raw, str) else ""
        state_raw = payload.get("state")
        state = state_raw if isinstance(state_raw, str) else "open"
        return RoadmapPRPublication(
            number=number,
            html_url=html_url,
            title=title,
            body=body,
            state=state,
        )

    @staticmethod
    def _require_int(payload: dict[str, Any], *, key: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SCMAdapterError(f"GitHub API response is missing integer field '{key}'")
        return value

    @staticmethod
    def _require_str(payload: dict[str, Any], *, key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise SCMAdapterError(f"GitHub API response is missing string field '{key}'")
        return value
