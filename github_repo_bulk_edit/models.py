from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class LanguageBreakdown:
    name: str
    chars: int


@dataclass(slots=True)
class Repository:
    id: int
    owner: str
    name: str
    private: bool
    archived: bool
    html_url: str
    visibility: str = ""
    created_at: str = ""
    updated_at: str = ""
    language: str = ""
    languages: tuple[LanguageBreakdown, ...] = ()
    commit_count_label: str = ""
    metadata_error: str = ""
    metadata_commit_pages: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Repository":
        owner = data.get("owner") or {}
        return cls(
            id=int(data["id"]),
            owner=str(owner["login"]),
            name=str(data["name"]),
            private=bool(data.get("private", False)),
            archived=bool(data.get("archived", False)),
            html_url=str(data.get("html_url", "")),
            visibility=str(data.get("visibility") or ("private" if data.get("private") else "public")),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            language=str(data.get("language") or ""),
        )


@dataclass(slots=True)
class RepoEdit:
    repo: Repository
    name_active: bool = False
    name_draft: str | None = None
    private_active: bool = False
    private_draft: bool | None = None
    archived_active: bool = False
    archived_draft: bool | None = None
    delete_active: bool = False

    def start_name_insert(self, initial: str | None = None) -> str:
        """Activate name editing with the original name as the default buffer."""
        self.name_active = True
        self.name_draft = self.repo.name if initial is None else initial
        return self.name_draft

    def set_name(self, name: str) -> None:
        cleaned = name.strip()
        self.name_draft = cleaned
        self.name_active = cleaned != self.repo.name

    def toggle_name(self) -> None:
        if self.name_active:
            self.name_active = False
            return
        if self.name_draft is None:
            self.name_draft = self.repo.name
        self.name_active = True

    def toggle_private(self) -> None:
        if not self.private_active:
            self.private_draft = not self.repo.private
            self.private_active = True
            return
        next_value = not bool(self.private_draft)
        self.private_draft = next_value
        self.private_active = next_value != self.repo.private

    def toggle_archived(self) -> None:
        if not self.archived_active:
            self.archived_draft = not self.repo.archived
            self.archived_active = True
            return
        next_value = not bool(self.archived_draft)
        self.archived_draft = next_value
        self.archived_active = next_value != self.repo.archived

    def toggle_delete(self) -> None:
        self.delete_active = not self.delete_active

    def reset(self) -> None:
        self.name_active = False
        self.name_draft = None
        self.private_active = False
        self.private_draft = None
        self.archived_active = False
        self.archived_draft = None
        self.delete_active = False

    @property
    def effective_name(self) -> str:
        if self.name_active and self.name_draft is not None:
            return self.name_draft
        return self.repo.name

    @property
    def effective_private(self) -> bool:
        if self.private_active and self.private_draft is not None:
            return self.private_draft
        return self.repo.private

    @property
    def effective_archived(self) -> bool:
        if self.archived_active and self.archived_draft is not None:
            return self.archived_draft
        return self.repo.archived

    @property
    def changed(self) -> bool:
        return self.delete_active or bool(self.patch())

    def patch(self) -> dict[str, Any]:
        if self.delete_active:
            return {}
        patch: dict[str, Any] = {}
        if self.name_active and self.name_draft and self.name_draft != self.repo.name:
            patch["name"] = self.name_draft
        if self.private_active and self.private_draft is not None and self.private_draft != self.repo.private:
            patch["private"] = self.private_draft
        if (
            self.archived_active
            and self.archived_draft is not None
            and self.archived_draft != self.repo.archived
        ):
            patch["archived"] = self.archived_draft
        return patch

    def summary(self) -> str:
        if self.delete_active:
            return f"delete {self.repo.full_name}"
        changes: list[str] = []
        patch = self.patch()
        if "name" in patch:
            changes.append(f"name {self.repo.name} -> {patch['name']}")
        if "private" in patch:
            changes.append(
                f"visibility {visibility_label(self.repo.private)} -> {visibility_label(bool(patch['private']))}"
            )
        if "archived" in patch:
            changes.append(
                f"archive {archive_label(self.repo.archived)} -> {archive_label(bool(patch['archived']))}"
            )
        return "; ".join(changes)

    def absorb_api_update(self, data: dict[str, Any]) -> None:
        self.repo = Repository.from_api(data)
        self.reset()


def visibility_label(private: bool) -> str:
    return "private" if private else "public"


def archive_label(archived: bool) -> str:
    return "archived" if archived else "not archived"
