from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import LanguageBreakdown, Repository

CACHE_VERSION = 1
DEFAULT_CACHE_FILE = ".github-repo-bulk-edit-cache.json"


@dataclass(frozen=True, slots=True)
class RepositoryCacheKey:
    api_url: str
    api_version: str
    affiliation: str
    visibility: str
    owner: str | None
    limit: int | None

    def as_json(self) -> dict[str, Any]:
        return {
            "api_url": self.api_url,
            "api_version": self.api_version,
            "affiliation": self.affiliation,
            "visibility": self.visibility,
            "owner": self.owner,
            "limit": self.limit,
        }


@dataclass(slots=True)
class CacheLoadResult:
    repositories: list[Repository]
    loaded: bool = False
    ignored_reason: str = ""


def load_repository_cache(path: Path, key: RepositoryCacheKey) -> CacheLoadResult:
    if not path.exists():
        return CacheLoadResult([], ignored_reason="missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CacheLoadResult([], ignored_reason="unreadable")
    if not isinstance(data, dict):
        return CacheLoadResult([], ignored_reason="invalid")
    if data.get("version") != CACHE_VERSION:
        return CacheLoadResult([], ignored_reason="version")
    if data.get("key") != key.as_json():
        return CacheLoadResult([], ignored_reason="key")
    entries = data.get("repositories")
    if not isinstance(entries, list):
        return CacheLoadResult([], ignored_reason="repositories")
    metadata_commit_pages = data.get("metadata_commit_pages")
    if not isinstance(metadata_commit_pages, int):
        metadata_commit_pages = None
    try:
        repos = [_repository_from_entry(entry, default_metadata_commit_pages=metadata_commit_pages) for entry in entries]
    except (KeyError, TypeError, ValueError):
        return CacheLoadResult([], ignored_reason="repositories")

    return CacheLoadResult(repositories=repos, loaded=True)


def save_repository_cache(
    path: Path,
    key: RepositoryCacheKey,
    repositories: list[Repository],
    *,
    metadata_commit_pages: int | None,
) -> None:
    payload = {
        "version": CACHE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "key": key.as_json(),
        "metadata_commit_pages": metadata_commit_pages,
        "repositories": [_repository_to_entry(repo) for repo in repositories],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _repository_to_entry(repo: Repository) -> dict[str, Any]:
    return {
        "id": repo.id,
        "owner": repo.owner,
        "name": repo.name,
        "private": repo.private,
        "archived": repo.archived,
        "html_url": repo.html_url,
        "visibility": repo.visibility,
        "created_at": repo.created_at,
        "updated_at": repo.updated_at,
        "language": repo.language,
        "languages": [
            {
                "name": language.name,
                "chars": language.chars,
            }
            for language in repo.languages
        ],
        "commit_count_label": repo.commit_count_label,
        "metadata_error": repo.metadata_error,
        "metadata_commit_pages": repo.metadata_commit_pages,
    }


def _repository_from_entry(entry: Any, *, default_metadata_commit_pages: int | None) -> Repository:
    if not isinstance(entry, dict):
        raise TypeError("repository entry must be an object")
    languages = entry.get("languages", [])
    if not isinstance(languages, list):
        raise TypeError("languages must be a list")
    metadata_commit_pages = entry.get("metadata_commit_pages", default_metadata_commit_pages)
    if not isinstance(metadata_commit_pages, int):
        metadata_commit_pages = None
    return Repository(
        id=int(entry["id"]),
        owner=str(entry["owner"]),
        name=str(entry["name"]),
        private=bool(entry.get("private", False)),
        archived=bool(entry.get("archived", False)),
        html_url=str(entry.get("html_url", "")),
        visibility=str(entry.get("visibility") or ("private" if entry.get("private") else "public")),
        created_at=str(entry.get("created_at") or ""),
        updated_at=str(entry.get("updated_at") or ""),
        language=str(entry.get("language") or ""),
        languages=tuple(_language_from_entry(language) for language in languages),
        commit_count_label=str(entry.get("commit_count_label") or ""),
        metadata_error=str(entry.get("metadata_error") or ""),
        metadata_commit_pages=metadata_commit_pages,
    )


def _language_from_entry(entry: Any) -> LanguageBreakdown:
    if not isinstance(entry, dict):
        raise TypeError("language entry must be an object")
    return LanguageBreakdown(name=str(entry["name"]), chars=int(entry["chars"]))
