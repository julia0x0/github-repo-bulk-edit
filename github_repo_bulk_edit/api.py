from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import DEFAULT_DOTENV_FILE, get_github_token
from .models import LanguageBreakdown, Repository

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
DEFAULT_METADATA_WORKERS = 16


class GitHubError(RuntimeError):
    pass


@dataclass(slots=True)
class GitHubClient:
    token: str
    api_url: str = DEFAULT_API_URL
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_env(
        cls,
        api_url: str = DEFAULT_API_URL,
        api_version: str | None = None,
        dotenv_path: str | os.PathLike[str] = DEFAULT_DOTENV_FILE,
    ) -> "GitHubClient":
        token = get_github_token(dotenv_path)
        if not token:
            raise GitHubError("Set GITHUB_TOKEN or GH_TOKEN in your environment or .env before running the tool.")
        return cls(token=token, api_url=api_url, api_version=api_version or DEFAULT_API_VERSION)

    def list_repositories(
        self,
        *,
        affiliation: str = "owner",
        visibility: str = "all",
        sort: str = "full_name",
        owner: str | None = None,
        limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
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
                    if progress is not None:
                        progress(len(repos), page)
                    return repos
            if progress is not None:
                progress(len(repos), page)
            if len(data) < 100:
                return repos
            page += 1

    def update_repository(self, repo: Repository, patch: dict[str, Any]) -> dict[str, Any]:
        if not patch:
            return {}
        owner = quote(repo.owner, safe="")
        name = quote(repo.name, safe="")
        return self.request("PATCH", f"/repos/{owner}/{name}", body=patch)

    def delete_repository(self, repo: Repository) -> None:
        owner = quote(repo.owner, safe="")
        name = quote(repo.name, safe="")
        self.request("DELETE", f"/repos/{owner}/{name}")

    def enrich_repository_metadata(
        self,
        repos: list[Repository],
        *,
        commit_pages: int = 2,
        workers: int = DEFAULT_METADATA_WORKERS,
        progress: Callable[[int, int, Repository], None] | None = None,
    ) -> None:
        total = len(repos)
        if total == 0:
            return
        worker_count = max(1, min(workers, total))
        if worker_count == 1:
            for index, repo in enumerate(repos, start=1):
                self._enrich_one_repository(repo, commit_pages=commit_pages)
                if progress is not None:
                    progress(index, total, repo)
            return

        completed = 0
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._enrich_one_repository, repo, commit_pages=commit_pages): repo for repo in repos
            }
            for future in as_completed(futures):
                repo = futures[future]
                future.result()
                completed += 1
                if progress is not None:
                    progress(completed, total, repo)

    def _enrich_one_repository(self, repo: Repository, *, commit_pages: int) -> None:
        errors: list[str] = []
        try:
            repo.languages = self.repository_languages(repo)
        except GitHubError as exc:
            errors.append(f"languages unavailable: {exc}")
        try:
            repo.commit_count_label = self.bounded_commit_count_label(repo, pages=commit_pages)
        except GitHubError as exc:
            errors.append(f"commits unavailable: {exc}")
        repo.metadata_error = "; ".join(errors)
        repo.metadata_commit_pages = commit_pages

    def repository_languages(self, repo: Repository) -> tuple[LanguageBreakdown, ...]:
        owner = quote(repo.owner, safe="")
        name = quote(repo.name, safe="")
        data = self.request("GET", f"/repos/{owner}/{name}/languages")
        if not isinstance(data, dict):
            raise GitHubError("GitHub returned an unexpected languages response.")
        languages = [
            LanguageBreakdown(name=str(language), chars=int(chars))
            for language, chars in data.items()
            if isinstance(chars, int) and chars > 0
        ]
        return tuple(sorted(languages, key=lambda language: language.chars, reverse=True))

    def bounded_commit_count_label(self, repo: Repository, *, pages: int = 2, per_page: int = 100) -> str:
        if pages <= 0:
            return ""
        owner = quote(repo.owner, safe="")
        name = quote(repo.name, safe="")
        total = 0
        for page in range(1, pages + 1):
            data = self.request(
                "GET",
                f"/repos/{owner}/{name}/commits",
                params={"per_page": str(per_page), "page": str(page)},
            )
            if not isinstance(data, list):
                raise GitHubError("GitHub returned an unexpected commits response.")
            total += len(data)
            if len(data) < per_page:
                return str(total)
        return f"{total}+"

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
