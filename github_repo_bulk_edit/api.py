from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import Repository

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    pass


@dataclass(slots=True)
class GitHubClient:
    token: str
    api_url: str = DEFAULT_API_URL
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_env(cls, api_url: str = DEFAULT_API_URL, api_version: str | None = None) -> "GitHubClient":
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise GitHubError("Set GITHUB_TOKEN or GH_TOKEN before running the tool.")
        return cls(token=token, api_url=api_url, api_version=api_version or DEFAULT_API_VERSION)

    def list_repositories(
        self,
        *,
        affiliation: str = "owner",
        visibility: str = "all",
        sort: str = "full_name",
        owner: str | None = None,
        limit: int | None = None,
    ) -> list[Repository]:
        params = {
            "affiliation": affiliation,
            "visibility": visibility,
            "sort": sort,
            "per_page": "100",
        }
        repos: list[Repository] = []
        page = 1
        while True:
            params["page"] = str(page)
            data = self.request("GET", "/user/repos", params=params)
            if not isinstance(data, list):
                raise GitHubError("GitHub returned an unexpected repository list response.")
            for item in data:
                repo = Repository.from_api(item)
                if owner and repo.owner.lower() != owner.lower():
                    continue
                repos.append(repo)
                if limit is not None and len(repos) >= limit:
                    return repos
            if len(data) < 100:
                return repos
            page += 1

    def update_repository(self, repo: Repository, patch: dict[str, Any]) -> dict[str, Any]:
        if not patch:
            return {}
        owner = quote(repo.owner, safe="")
        name = quote(repo.name, safe="")
        return self.request("PATCH", f"/repos/{owner}/{name}", body=patch)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.api_url.rstrip('/')}{path}{query}"
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        request = Request(url=url, data=payload, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("User-Agent", "github-repo-bulk-edit")
        request.add_header("X-GitHub-Api-Version", self.api_version)
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            raise GitHubError(self._format_http_error(exc)) from exc
        except URLError as exc:
            raise GitHubError(f"Could not reach GitHub: {exc.reason}") from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _format_http_error(exc: HTTPError) -> str:
        detail = ""
        try:
            raw = exc.read().decode("utf-8")
            if raw:
                parsed = json.loads(raw)
                message = parsed.get("message")
                errors = parsed.get("errors")
                if message:
                    detail = f": {message}"
                if errors:
                    detail = f"{detail} ({errors})"
        except Exception:
            detail = ""
        return f"GitHub API error {exc.code}{detail}"
