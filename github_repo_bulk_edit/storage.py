from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import RepoEdit

PLAN_VERSION = 1
DEFAULT_PLAN_FILE = ".github-repo-bulk-edit-plan.json"


@dataclass(slots=True)
class LoadResult:
    loaded: int = 0
    ignored: int = 0


def save_plan(path: Path, edits: list[RepoEdit]) -> int:
    entries = []
    for edit in edits:
        entry = _entry_from_edit(edit)
        if entry is not None:
            entries.append(entry)
    payload = {
        "version": PLAN_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "repositories": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    return sum(1 for edit in edits if edit.changed)


def load_plan(path: Path, edits: list[RepoEdit]) -> LoadResult:
    if not path.exists():
        return LoadResult()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LoadResult(ignored=1)
    if not isinstance(data, dict):
        return LoadResult(ignored=1)
    entries = data.get("repositories", [])
    if not isinstance(entries, list):
        return LoadResult(ignored=1)
    by_id = {edit.repo.id: edit for edit in edits}
    by_full_name = {edit.repo.full_name.lower(): edit for edit in edits}
    result = LoadResult()
    for entry in entries:
        if not isinstance(entry, dict):
            result.ignored += 1
            continue
        edit = _find_edit(entry, by_id, by_full_name)
        if edit is None:
            result.ignored += 1
            continue
        _apply_entry(edit, entry)
        result.loaded += 1
    return result


def clear_plan(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def plan_signature(edits: list[RepoEdit]) -> str:
    entries = []
    for edit in edits:
        entry = _entry_from_edit(edit)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda entry: (entry.get("id", 0), entry.get("owner", ""), entry.get("name", "")))
    return json.dumps(entries, sort_keys=True, separators=(",", ":"))


def _entry_from_edit(edit: RepoEdit) -> dict[str, Any] | None:
    has_state = any(
        (
            edit.name_draft is not None,
            edit.private_draft is not None,
            edit.archived_draft is not None,
            edit.delete_active,
        )
    )
    if not has_state and not edit.changed:
        return None
    return {
        "id": edit.repo.id,
        "owner": edit.repo.owner,
        "name": edit.repo.name,
        "name_active": edit.name_active,
        "name_draft": edit.name_draft,
        "private_active": edit.private_active,
        "private_draft": edit.private_draft,
        "archived_active": edit.archived_active,
        "archived_draft": edit.archived_draft,
        "delete_active": edit.delete_active,
        "patch": edit.patch(),
    }


def _find_edit(
    entry: dict[str, Any],
    by_id: dict[int, RepoEdit],
    by_full_name: dict[str, RepoEdit],
) -> RepoEdit | None:
    repo_id = entry.get("id")
    if isinstance(repo_id, int) and repo_id in by_id:
        return by_id[repo_id]
    owner = entry.get("owner")
    name = entry.get("name")
    if isinstance(owner, str) and isinstance(name, str):
        return by_full_name.get(f"{owner}/{name}".lower())
    return None


def _apply_entry(edit: RepoEdit, entry: dict[str, Any]) -> None:
    edit.name_draft = _optional_str(entry.get("name_draft"))
    edit.name_active = bool(entry.get("name_active")) and edit.name_draft != edit.repo.name
    private_draft = entry.get("private_draft")
    edit.private_draft = private_draft if isinstance(private_draft, bool) else None
    edit.private_active = bool(entry.get("private_active")) and edit.private_draft != edit.repo.private
    archived_draft = entry.get("archived_draft")
    edit.archived_draft = archived_draft if isinstance(archived_draft, bool) else None
    edit.archived_active = bool(entry.get("archived_active")) and edit.archived_draft != edit.repo.archived
    edit.delete_active = bool(entry.get("delete_active"))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
