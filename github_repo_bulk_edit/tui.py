from __future__ import annotations

import curses
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api import GitHubClient, GitHubError
from .config import AppConfig, default_app_config
from .models import RepoEdit, archive_label, visibility_label
from .storage import clear_plan, plan_signature, save_plan

FIELD_NAME_ORIGINAL = 0
FIELD_NAME_DRAFT = 1
FIELD_PRIVATE = 2
FIELD_ARCHIVED = 3

NAME_FIELDS = {FIELD_NAME_ORIGINAL, FIELD_NAME_DRAFT}

MIN_NAME_WIDTH = 46
VISIBILITY_WIDTH = 18
ARCHIVE_WIDTH = 14
CREATED_WIDTH = 12
UPDATED_WIDTH = 12
COMMITS_WIDTH = 10
LANGUAGE_NAME_WIDTH = 10
LANGUAGE_PERCENT_WIDTH = 4
LANGUAGE_CHARS_WIDTH = 6
LANGUAGE_LOC_WIDTH = 8
LANGUAGE_WIDTH = LANGUAGE_NAME_WIDTH + LANGUAGE_PERCENT_WIDTH + LANGUAGE_CHARS_WIDTH + LANGUAGE_LOC_WIDTH + 3
FIELD_GAP = 2
ESTIMATED_CHARS_PER_LINE = 80
SORT_MODES = ("name", "updated", "created", "commits", "primary language", "total chars")


@dataclass(slots=True)
class ViewState:
    row: int = 0
    field: int = 0
    offset: int = 0
    status: str = ""
    help_visible: bool = False
    sort_index: int = -1


@dataclass(slots=True)
class ApplyItem:
    edit: RepoEdit
    call: str
    status: str = "queued"
    message: str = ""


@dataclass(frozen=True, slots=True)
class RowLayout:
    name_width: int
    visibility_x: int
    archive_x: int
    created_x: int
    updated_x: int
    commits_x: int
    primary_lang_x: int
    secondary_lang_x: int


class RepoBulkEditApp:
    def __init__(
        self,
        *,
        client: GitHubClient,
        edits: list[RepoEdit],
        plan_file: Path,
        loaded_plan: bool = False,
        config: AppConfig | None = None,
        cache_file: Path | None = None,
    ) -> None:
        self.client = client
        self.edits = edits
        self.plan_file = plan_file
        self.cache_file = cache_file
        self.saved_signature = plan_signature(edits) if loaded_plan else ""
        self.state = ViewState()
        self.config = config or default_app_config()

    def run(self) -> int:
        return curses.wrapper(self._run)

    def _run(self, screen: curses.window) -> int:
        curses.curs_set(0)
        curses.use_default_colors()
        self._init_colors()
        screen.keypad(True)
        while True:
            self._draw(screen)
            key = screen.getch()
            result = self._handle_key(screen, key)
            if result is not None:
                return result

    def _init_colors(self) -> None:
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_CYAN, -1)
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_RED)

    def _handle_key(self, screen: curses.window, key: int) -> int | None:
        self.state.status = ""
        if self.state.help_visible:
            self.state.help_visible = False
            return None
        if self._key_matches("quit", key):
            return self._quit(screen, force=False)
        if self._key_matches("help", key):
            self.state.help_visible = True
            return None
        if self._key_matches("command_prompt", key):
            return self._command_prompt(screen)
        if self._key_matches("move_down", key):
            self._move_row(1)
        elif self._key_matches("move_up", key):
            self._move_row(-1)
        elif self._key_matches("move_left", key):
            self._move_field(-1)
        elif self._key_matches("move_right", key):
            self._move_field(1)
        elif self._key_matches("first_row", key):
            self.state.row = 0
            self._normalize_field()
            self._keep_selection_visible()
        elif self._key_matches("last_row", key):
            self.state.row = len(self.edits) - 1
            self._normalize_field()
            self._keep_selection_visible()
        elif self._key_matches("activate", key) or self._key_matches("edit", key):
            self._activate_current_field(screen, mode="edit")
        elif self._key_matches("toggle", key):
            self._activate_current_field(screen, mode="toggle")
        elif self._key_matches("open_repository", key):
            self._open_selected_repository()
        elif self._key_matches("mark_delete", key):
            self._toggle_selected_delete()
        elif self._key_matches("toggle_archive", key):
            self._toggle_selected_archive()
        elif self._key_matches("toggle_visibility", key):
            self._toggle_selected_visibility()
        elif self._key_matches("cycle_sort", key):
            self._cycle_sort()
        elif self._key_matches("reset_row", key):
            self.edits[self.state.row].reset()
            self._normalize_field()
            self.state.status = f"Reset {self.edits[self.state.row].repo.full_name}"
        elif self._key_matches("reset_all", key):
            if self._confirm_text(screen, "Reset every staged edit? Type reset to continue: ", "reset"):
                for edit in self.edits:
                    edit.reset()
                self._normalize_field()
                self.state.status = "Reset every staged edit"
        else:
            self.state.status = "Unknown key. Press ? for help."
        return None

    def _move_row(self, delta: int) -> None:
        self.state.row = max(0, min(len(self.edits) - 1, self.state.row + delta))
        self._normalize_field()
        self._keep_selection_visible()

    def _move_field(self, delta: int) -> None:
        self._normalize_field()
        fields = self._selectable_fields(self.edits[self.state.row])
        index = fields.index(self.state.field)
        self.state.field = fields[(index + delta) % len(fields)]

    def _selectable_fields(self, edit: RepoEdit) -> list[int]:
        fields = [FIELD_NAME_ORIGINAL]
        if edit.name_active:
            fields.append(FIELD_NAME_DRAFT)
        fields.append(FIELD_PRIVATE)
        fields.append(FIELD_ARCHIVED)
        return fields

    def _normalize_field(self) -> None:
        edit = self.edits[self.state.row]
        fields = self._selectable_fields(edit)
        if self.state.field in fields:
            return
        self.state.field = FIELD_NAME_ORIGINAL if self.state.field == FIELD_NAME_DRAFT else fields[0]

    def _keep_selection_visible(self) -> None:
        screen_height = getattr(curses, "LINES", len(self.edits) + 6)
        usable_height = self._visible_repo_capacity(screen_height)
        if self.state.row < self.state.offset:
            self.state.offset = self.state.row
        elif self.state.row >= self.state.offset + usable_height:
            self.state.offset = self.state.row - usable_height + 1

    @staticmethod
    def _visible_repo_capacity(screen_height: int) -> int:
        return max(1, screen_height - 6)

    def _activate_current_field(self, screen: curses.window, *, mode: str) -> None:
        self._normalize_field()
        edit = self.edits[self.state.row]
        if self.state.field in NAME_FIELDS:
            if mode == "toggle":
                if self.state.field == FIELD_NAME_ORIGINAL and not edit.name_active:
                    self._edit_selected_name(screen, edit)
                    return
                self._toggle_selected_name(edit)
                return
            self._edit_selected_name(screen, edit)
        elif self.state.field == FIELD_PRIVATE:
            edit.toggle_private()
            self._normalize_field()
        elif self.state.field == FIELD_ARCHIVED:
            edit.toggle_archived()
            self._normalize_field()

    def _toggle_selected_name(self, edit: RepoEdit) -> None:
        had_staged_name = "name" in edit.patch()
        was_draft_selected = self.state.field == FIELD_NAME_DRAFT
        edit.toggle_name()
        self.state.field = FIELD_NAME_ORIGINAL if was_draft_selected else self.state.field
        self._normalize_field()
        has_staged_name = "name" in edit.patch()
        if has_staged_name:
            self.state.status = f"Restored name change for {edit.repo.full_name}"
        elif had_staged_name:
            self.state.status = "Name change unstaged; saved draft will not be applied unless restored"
        elif edit.name_active:
            self.state.status = "Name draft shown; press i or Enter to type a new name"
        else:
            self.state.status = "No rename staged"

    def _edit_selected_name(self, screen: curses.window, edit: RepoEdit) -> None:
        previous_active = edit.name_active
        previous_draft = edit.name_draft
        initial = edit.name_draft if edit.name_draft is not None else edit.repo.name
        edit.start_name_insert(initial)
        self.state.field = FIELD_NAME_DRAFT
        new_name = self._edit_name(screen, initial)
        if new_name is None:
            edit.name_active = previous_active
            edit.name_draft = previous_draft
            self._normalize_field()
            self.state.status = "Name edit cancelled"
            return
        edit.set_name(new_name)
        self._normalize_field()
        if "name" in edit.patch():
            self.state.field = FIELD_NAME_DRAFT
            self.state.status = f"Staged name for {edit.repo.full_name}"
        else:
            self.state.field = FIELD_NAME_ORIGINAL
            self.state.status = "Name unchanged"

    def _open_selected_repository(self) -> None:
        self._normalize_field()
        edit = self.edits[self.state.row]
        if self.state.field != FIELD_NAME_ORIGINAL:
            self.state.status = "Open is only available on the original repo name"
            return
        if not edit.repo.html_url:
            self.state.status = f"No repository URL available for {edit.repo.full_name}"
            return

        command = [part.replace("{url}", edit.repo.html_url) for part in self.config.browser_open_command]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            self.state.status = f"Could not open {edit.repo.html_url}: {exc}"
            return
        self.state.status = f"Opened {edit.repo.html_url}"

    def _toggle_selected_delete(self) -> None:
        self._normalize_field()
        edit = self.edits[self.state.row]
        edit.toggle_delete()
        if edit.delete_active:
            self.state.status = f"Marked {edit.repo.full_name} for deletion"
        else:
            self.state.status = f"Unmarked {edit.repo.full_name} for deletion"

    def _toggle_selected_archive(self) -> None:
        edit = self.edits[self.state.row]
        edit.toggle_archived()
        self.state.status = f"Archive set to {archive_label(edit.effective_archived)} for {edit.repo.full_name}"

    def _toggle_selected_visibility(self) -> None:
        edit = self.edits[self.state.row]
        edit.toggle_private()
        self.state.status = f"Visibility set to {visibility_label(edit.effective_private)} for {edit.repo.full_name}"

    def _cycle_sort(self) -> None:
        selected_edit = self.edits[self.state.row]
        self.state.sort_index = (self.state.sort_index + 1) % len(SORT_MODES)
        mode = SORT_MODES[self.state.sort_index]
        self.edits.sort(key=lambda edit: self._sort_key(edit, mode))
        self.state.row = next(index for index, edit in enumerate(self.edits) if edit is selected_edit)
        self._normalize_field()
        self._keep_selection_visible()
        self.state.status = f"Sorted by {mode}"

    def _sort_key(self, edit: RepoEdit, mode: str) -> tuple[Any, ...]:
        name_key = edit.repo.full_name.lower()
        if mode == "name":
            return (name_key,)
        if mode == "updated":
            return (-self._date_sort_value(edit.repo.updated_at), name_key)
        if mode == "created":
            return (-self._date_sort_value(edit.repo.created_at), name_key)
        if mode == "commits":
            count, is_plus = self._commit_sort_value(edit.repo.commit_count_label)
            return (-count, 0 if is_plus else 1, name_key)
        if mode == "primary language":
            language = self._primary_language_name(edit)
            return (language == "", language.lower(), name_key)
        if mode == "total chars":
            return (-self._total_language_chars(edit), name_key)
        return (name_key,)

    @staticmethod
    def _date_sort_value(value: str) -> float:
        if not value:
            return 0.0
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @staticmethod
    def _commit_sort_value(value: str) -> tuple[int, bool]:
        stripped = value.strip()
        if not stripped:
            return 0, False
        is_plus = stripped.endswith("+")
        if is_plus:
            stripped = stripped[:-1]
        try:
            return int(stripped), is_plus
        except ValueError:
            return 0, is_plus

    @staticmethod
    def _primary_language_name(edit: RepoEdit) -> str:
        if edit.repo.languages:
            return edit.repo.languages[0].name
        return edit.repo.language

    @staticmethod
    def _total_language_chars(edit: RepoEdit) -> int:
        return sum(language.chars for language in edit.repo.languages)

    def _edit_name(self, screen: curses.window, initial: str) -> str | None:
        buffer = list(initial)
        cursor = len(buffer)
        while True:
            self._draw(screen, edit_buffer="".join(buffer), edit_cursor=cursor)
            key = screen.getch()
            if self._key_matches("name_edit_submit", key) or self._key_matches("name_edit_exit", key):
                return "".join(buffer)
            if self._key_matches("name_edit_cancel", key):
                return None

            matched, replay_keys = self._consume_key_sequence(screen, key, "name_edit_exit")
            if matched:
                return "".join(buffer)
            for replay_key in replay_keys:
                action, cursor = self._apply_name_edit_key(replay_key, buffer, cursor)
                if action == "cancel":
                    return None
                if action == "submit":
                    return "".join(buffer)

    def _apply_name_edit_key(self, key: int, buffer: list[str], cursor: int) -> tuple[str | None, int]:
        if self._key_matches("name_edit_submit", key):
            return "submit", cursor
        if self._key_matches("name_edit_cancel", key):
            return "cancel", cursor
        if self._key_matches("name_edit_backspace", key):
            if cursor > 0:
                del buffer[cursor - 1]
                cursor -= 1
            return None, cursor
        if self._key_matches("name_edit_delete", key):
            if cursor < len(buffer):
                del buffer[cursor]
            return None, cursor
        if self._key_matches("name_edit_left", key):
            return None, max(0, cursor - 1)
        if self._key_matches("name_edit_right", key):
            return None, min(len(buffer), cursor + 1)
        if self._key_matches("name_edit_home", key):
            return None, 0
        if self._key_matches("name_edit_end", key):
            return None, len(buffer)
        if 32 <= key <= 126:
            buffer.insert(cursor, chr(key))
            cursor += 1
        return None, cursor

    def _consume_key_sequence(self, screen: curses.window, first_key: int, action: str) -> tuple[bool, list[int]]:
        first_name = self._key_name(first_key)
        candidates = [
            sequence
            for sequence in self.config.keybindings.get(action, ())
            if len(sequence) > 1 and sequence[0] == first_name
        ]
        if not candidates:
            return False, [first_key]

        keys = [first_key]
        names = [first_name]
        while True:
            current = tuple(names)
            if current in candidates:
                return True, []
            if not any(sequence[: len(current)] == current for sequence in candidates):
                return False, keys

            next_key = self._getch_with_timeout(screen, self.config.key_sequence_timeout_ms)
            if next_key == -1:
                return False, keys
            keys.append(next_key)
            names.append(self._key_name(next_key))

    @staticmethod
    def _getch_with_timeout(screen: curses.window, timeout_ms: int) -> int:
        try:
            screen.timeout(timeout_ms)
            return screen.getch()
        finally:
            screen.timeout(-1)

    def _command_prompt(self, screen: curses.window) -> int | None:
        command = self._read_prompt(screen, ":")
        if command is None:
            return None
        command = command.strip()
        if command in ("q!", "quit!"):
            return 0
        if command in ("q", "quit"):
            return self._quit(screen, force=False)
        if command in ("w", "write"):
            self._write_plan()
            return None
        if command in ("wq", "x"):
            if not self._write_plan():
                return None
            if self._apply_changes(screen):
                return 0
            return None
        self.state.status = f"Unknown command: {command}"
        return None

    def _write_plan(self) -> bool:
        try:
            changed_count = save_plan(self.plan_file, self.edits)
        except OSError as exc:
            self.state.status = f"Could not save {self.plan_file}: {exc}"
            return False
        self.saved_signature = plan_signature(self.edits)
        self.state.status = f"Saved {changed_count} staged repo update(s) to {self.plan_file}"
        return True

    def _quit(self, screen: curses.window, *, force: bool) -> int | None:
        if force:
            return 0
        if not self._changed_count():
            return 0
        if self._current_plan_is_saved():
            if self._apply_changes(screen):
                return 0
            return None
        self.state.status = "Unsaved staged edits. Use :w to save, :wq to save+apply, or :q! to discard."
        return None

    def _current_plan_is_saved(self) -> bool:
        return bool(self.saved_signature) and self.saved_signature == plan_signature(self.edits) and self.plan_file.exists()

    def _apply_changes(self, screen: curses.window) -> bool:
        changed = [edit for edit in self.edits if edit.changed]
        if not changed:
            clear_plan(self.plan_file)
            self.saved_signature = ""
            self.state.status = "No staged changes to apply"
            return True
        items = [ApplyItem(edit=edit, call=self._api_call_summary(edit)) for edit in changed]
        if not self._confirm_apply_summary(screen, items):
            self.state.status = "Apply cancelled"
            return False
        failures = self._run_apply_progress(screen, items)
        self._invalidate_cache_after_apply(items)
        if plan_signature(self.edits) != "[]":
            self._write_plan()
        else:
            clear_plan(self.plan_file)
            self.saved_signature = ""
        if failures:
            self.state.status = f"Applied with {len(failures)} failure(s)"
            self._show_failures(screen, failures)
            return False
        self.state.status = f"Applied {len(changed)} repo update(s)"
        return True

    def _invalidate_cache_after_apply(self, items: list[ApplyItem]) -> None:
        if self.cache_file is None or not any(item.status == "done" for item in items):
            return
        try:
            self.cache_file.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    def _run_apply_progress(self, screen: curses.window, items: list[ApplyItem]) -> list[str]:
        failures: list[str] = []
        for index, item in enumerate(items, start=1):
            item.status = "running"
            item.message = "started"
            self._draw_apply_progress(screen, items, index - 1)
            try:
                if item.edit.delete_active:
                    self.client.delete_repository(item.edit.repo)
                    item.edit.reset()
                else:
                    updated = self.client.update_repository(item.edit.repo, item.edit.patch())
                    item.edit.absorb_api_update(updated)
                item.status = "done"
                item.message = "confirmed"
            except GitHubError as exc:
                item.status = "error"
                item.message = str(exc)
                failures.append(f"{item.edit.repo.full_name}: {exc}")
            self._draw_apply_progress(screen, items, index)
        return failures

    def _api_call_summary(self, edit: RepoEdit) -> str:
        if edit.delete_active:
            return f"DELETE /repos/{edit.repo.owner}/{edit.repo.name}"
        patch = json.dumps(edit.patch(), sort_keys=True)
        return f"PATCH /repos/{edit.repo.owner}/{edit.repo.name} {patch}"

    def _confirm_apply_summary(self, screen: curses.window, items: list[ApplyItem]) -> bool:
        prompt = f"Apply {len(items)} GitHub API update(s)? Type apply to continue: "
        answer = self._read_summary_prompt(screen, items, prompt)
        return answer == "apply"

    def _read_summary_prompt(self, screen: curses.window, items: list[ApplyItem], prompt: str) -> str | None:
        buffer: list[str] = []
        offset = 0
        while True:
            available = max(1, screen.getmaxyx()[0] - 6)
            self._draw_apply_summary(screen, items, prompt + "".join(buffer), offset)
            key = screen.getch()
            if self._key_matches("prompt_cancel", key):
                return None
            if self._key_matches("page_down", key):
                offset = min(max(0, len(items) - available), offset + available)
                continue
            if self._key_matches("page_up", key):
                offset = max(0, offset - available)
                continue
            if self._key_matches("prompt_submit", key):
                return "".join(buffer)
            if self._key_matches("prompt_backspace", key):
                if buffer:
                    buffer.pop()
                continue
            if 32 <= key <= 126:
                buffer.append(chr(key))

    def _draw_apply_summary(
        self,
        screen: curses.window,
        items: list[ApplyItem],
        prompt: str,
        offset: int,
    ) -> None:
        screen.erase()
        max_y, max_x = screen.getmaxyx()
        self._addstr(screen, 0, 0, "Review GitHub API updates", curses.A_BOLD)
        self._addstr(screen, 1, 0, "Nothing has been sent yet. The saved plan will be applied only after confirmation.")
        available = max(1, max_y - 6)
        visible = items[offset : offset + available]
        for index, item in enumerate(visible, start=offset + 1):
            line = f"{index}. {item.call}"
            self._addstr(screen, index - offset + 2, 0, line[: max_x - 1], curses.color_pair(5))
        if items:
            end = min(len(items), offset + available)
            footer = f"Showing {offset + 1}-{end} of {len(items)}. PgUp/PgDn scroll."
            self._addstr(screen, max_y - 3, 0, footer[: max_x - 1], curses.A_DIM)
        self._addstr(screen, max_y - 1, 0, prompt[: max_x - 1], curses.A_REVERSE)
        screen.refresh()

    def _draw_apply_progress(self, screen: curses.window, items: list[ApplyItem], completed: int) -> None:
        screen.erase()
        max_y, max_x = screen.getmaxyx()
        total = len(items)
        self._addstr(screen, 0, 0, f"Applying GitHub updates {completed}/{total}", curses.A_BOLD)
        self._addstr(screen, 1, 0, self._progress_bar(completed, total, max(10, max_x - 20))[: max_x - 1])
        available = max(1, max_y - 4)
        start = max(0, min(max(0, completed - available + 1), max(0, total - available)))
        visible = items[start : start + available]
        for index, item in enumerate(visible, start=start + 1):
            attr = 0
            label = "[queued]"
            if item.status == "running":
                label = "[start]"
                attr = curses.color_pair(3)
            elif item.status == "done":
                label = "[ok]"
                attr = curses.color_pair(2)
            elif item.status == "error":
                label = "[error]"
                attr = curses.color_pair(4)
            detail = item.message
            line = f"{index}. {label} {item.call} {detail}".strip()
            self._addstr(screen, 3 + index - start - 1, 0, line[: max_x - 1], attr)
        screen.refresh()

    @staticmethod
    def _progress_bar(completed: int, total: int, width: int) -> str:
        if total <= 0:
            return "[] 0%"
        bar_width = max(4, min(width, 60))
        filled = int(bar_width * completed / total)
        percent = int(100 * completed / total)
        return f"[{'#' * filled}{'.' * (bar_width - filled)}] {percent}%"

    def _changed_count(self) -> int:
        return sum(1 for edit in self.edits if edit.changed)

    def _confirm_text(self, screen: curses.window, prompt: str, expected: str) -> bool:
        answer = self._read_prompt(screen, prompt)
        return answer == expected

    def _read_prompt(self, screen: curses.window, prompt: str) -> str | None:
        buffer: list[str] = []
        while True:
            self._draw(screen, prompt=prompt + "".join(buffer))
            key = screen.getch()
            if self._key_matches("prompt_cancel", key):
                return None
            if self._key_matches("prompt_submit", key):
                return "".join(buffer)
            if self._key_matches("prompt_backspace", key):
                if buffer:
                    buffer.pop()
                continue
            if 32 <= key <= 126:
                buffer.append(chr(key))

    def _show_failures(self, screen: curses.window, failures: list[str]) -> None:
        max_y, max_x = screen.getmaxyx()
        screen.erase()
        self._addstr(screen, 0, 0, "Failures", curses.A_BOLD)
        for index, failure in enumerate(failures[: max_y - 3], start=1):
            self._addstr(screen, index, 0, failure[: max_x - 1], curses.color_pair(4))
        self._addstr(screen, max_y - 1, 0, "Press any key to continue")
        screen.refresh()
        screen.getch()

    def _draw(
        self,
        screen: curses.window,
        *,
        prompt: str | None = None,
        edit_buffer: str | None = None,
        edit_cursor: int = 0,
    ) -> None:
        screen.erase()
        max_y, max_x = screen.getmaxyx()
        self._normalize_field()
        changed = self._changed_count()
        title = (
            f"github-repo-bulk-edit  repos:{len(self.edits)}  staged:{changed}  "
            f"plan:{self._save_status_label()}  sort:{self._sort_status_label()}"
        )
        self._addstr(screen, 0, 0, title[: max_x - 1], curses.A_BOLD)
        self._draw_header(screen, max_x, edit_buffer)
        usable_height = self._visible_repo_capacity(max_y)
        self._keep_selection_visible()
        visible = self.edits[self.state.offset : self.state.offset + usable_height]
        cursor_position: tuple[int, int] | None = None
        bottom = max_y - 2
        for index, edit in enumerate(visible):
            row_number = self.state.offset + index
            y = 3 + index
            selected = row_number == self.state.row
            row_cursor = self._draw_row(
                screen,
                y,
                max_x,
                edit,
                selected,
                edit_buffer if selected else None,
                edit_cursor if selected else 0,
            )
            if row_cursor is not None:
                cursor_position = row_cursor
        if edit_buffer is not None:
            status = self._name_edit_status()
        else:
            status = prompt or self.state.status or "j/k rows  h/l values  s sort  i/Enter edit  Space toggle  o open  :w save  :wq apply  ? help"
        self._addstr(screen, bottom, 0, status[: max_x - 1], curses.A_REVERSE if prompt else 0)
        if edit_buffer is not None and cursor_position is not None:
            self._addstr(screen, max_y - 1, 0, "Enter/Esc saves"[: max_x - 1], curses.A_DIM)
            try:
                screen.move(*cursor_position)
                curses.curs_set(1)
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        if self.state.help_visible:
            self._draw_help(screen)
        screen.refresh()

    def _draw_row(
        self,
        screen: curses.window,
        y: int,
        max_x: int,
        edit: RepoEdit,
        selected: bool,
        edit_buffer: str | None,
        edit_cursor: int = 0,
    ) -> tuple[int, int] | None:
        cursor_position: tuple[int, int] | None = None
        name_draft = None
        if edit_buffer is not None:
            name_draft = edit_buffer
        elif edit.name_active:
            name_draft = edit.name_draft or edit.repo.name
        layout = self._row_layout(edit, name_draft)
        cursor_position = self._draw_value_pair(
            screen,
            y,
            max_x,
            0,
            layout.name_width,
            edit,
            edit.repo.name,
            FIELD_NAME_ORIGINAL,
            name_draft,
            FIELD_NAME_DRAFT,
            selected,
            edit_cursor if edit_buffer is not None else None,
        )

        self._draw_single_value(
            screen,
            y,
            max_x,
            layout.visibility_x,
            VISIBILITY_WIDTH,
            edit,
            visibility_label(edit.effective_private),
            FIELD_PRIVATE,
            selected,
        )

        self._draw_single_value(
            screen,
            y,
            max_x,
            layout.archive_x,
            ARCHIVE_WIDTH,
            edit,
            archive_label(edit.effective_archived),
            FIELD_ARCHIVED,
            selected,
        )
        self._draw_dim_value(
            screen,
            y,
            max_x,
            layout.created_x,
            CREATED_WIDTH,
            self._format_github_date(edit.repo.created_at),
            edit,
        )
        self._draw_dim_value(
            screen,
            y,
            max_x,
            layout.updated_x,
            UPDATED_WIDTH,
            self._format_github_date(edit.repo.updated_at),
            edit,
        )
        self._draw_dim_value(
            screen,
            y,
            max_x,
            layout.commits_x,
            COMMITS_WIDTH,
            self._commit_count_text(edit),
            edit,
        )
        self._draw_dim_value(
            screen,
            y,
            max_x,
            layout.primary_lang_x,
            LANGUAGE_WIDTH,
            self._language_column_text(edit, 0),
            edit,
        )
        self._draw_dim_value(
            screen,
            y,
            max_x,
            layout.secondary_lang_x,
            LANGUAGE_WIDTH,
            self._language_column_text(edit, 1),
            edit,
        )
        return cursor_position

    def _draw_header(self, screen: curses.window, max_x: int, edit_buffer: str | None) -> None:
        current = self.edits[self.state.row]
        name_draft = edit_buffer if edit_buffer is not None else current.name_draft if current.name_active else None
        layout = self._row_layout(current, name_draft)
        self._addstr(screen, 2, 0, "repo"[: max_x - 1], curses.A_DIM)
        self._addstr(screen, 2, layout.visibility_x, "visibility"[: max(0, max_x - layout.visibility_x - 1)], curses.A_DIM)
        self._addstr(screen, 2, layout.archive_x, "archive"[: max(0, max_x - layout.archive_x - 1)], curses.A_DIM)
        self._addstr(screen, 2, layout.created_x, "created"[: max(0, max_x - layout.created_x - 1)], curses.A_DIM)
        self._addstr(screen, 2, layout.updated_x, "updated"[: max(0, max_x - layout.updated_x - 1)], curses.A_DIM)
        self._addstr(screen, 2, layout.commits_x, "commits"[: max(0, max_x - layout.commits_x - 1)], curses.A_DIM)
        self._addstr(screen, 2, layout.primary_lang_x, "primary lang"[: max(0, max_x - layout.primary_lang_x - 1)], curses.A_DIM)
        self._addstr(screen, 2, layout.secondary_lang_x, "secondary lang"[: max(0, max_x - layout.secondary_lang_x - 1)], curses.A_DIM)

    @staticmethod
    def _commit_count_text(edit: RepoEdit) -> str:
        if not edit.repo.commit_count_label:
            return ""
        return edit.repo.commit_count_label

    def _language_column_text(self, edit: RepoEdit, index: int) -> str:
        if index > 1:
            return ""
        if not edit.repo.languages:
            return edit.repo.language if index == 0 else ""
        total_chars = sum(language.chars for language in edit.repo.languages)
        if total_chars <= 0:
            return ""
        try:
            language = edit.repo.languages[index]
        except IndexError:
            return ""
        percent = int(round(language.chars * 100 / total_chars))
        estimated_lines = max(1, round(language.chars / ESTIMATED_CHARS_PER_LINE))
        name = self._fit_column_text(language.name, LANGUAGE_NAME_WIDTH)
        percent_text = f"{percent}%"
        chars_text = f"{self._compact_count(language.chars)}c"
        loc_text = f"~{self._compact_count(estimated_lines)}loc"
        return (
            f"{name:<{LANGUAGE_NAME_WIDTH}} "
            f"{percent_text:>{LANGUAGE_PERCENT_WIDTH}} "
            f"{chars_text:>{LANGUAGE_CHARS_WIDTH}} "
            f"{loc_text:>{LANGUAGE_LOC_WIDTH}}"
        )

    @staticmethod
    def _format_github_date(value: str) -> str:
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:10]
        return parsed.date().isoformat()

    @staticmethod
    def _compact_count(value: int) -> str:
        absolute = abs(value)
        if absolute >= 1_000_000:
            return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "m"
        if absolute >= 1_000:
            return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
        return str(value)

    @staticmethod
    def _fit_column_text(value: str, width: int) -> str:
        if len(value) <= width:
            return value
        if width <= 1:
            return value[:width]
        return value[: width - 1] + "~"

    def _row_layout(self, edit: RepoEdit, name_draft: str | None) -> RowLayout:
        name_width = max(MIN_NAME_WIDTH, self._name_pair_width(edit.repo.name, name_draft))
        visibility_x = name_width + FIELD_GAP
        archive_x = visibility_x + VISIBILITY_WIDTH + FIELD_GAP
        created_x = archive_x + ARCHIVE_WIDTH + FIELD_GAP
        updated_x = created_x + CREATED_WIDTH + FIELD_GAP
        commits_x = updated_x + UPDATED_WIDTH + FIELD_GAP
        primary_lang_x = commits_x + COMMITS_WIDTH + FIELD_GAP
        secondary_lang_x = primary_lang_x + LANGUAGE_WIDTH + FIELD_GAP
        return RowLayout(
            name_width=name_width,
            visibility_x=visibility_x,
            archive_x=archive_x,
            created_x=created_x,
            updated_x=updated_x,
            commits_x=commits_x,
            primary_lang_x=primary_lang_x,
            secondary_lang_x=secondary_lang_x,
        )

    @staticmethod
    def _name_pair_width(original_name: str, draft_name: str | None) -> int:
        if draft_name is None:
            return len(original_name)
        return len(original_name) + len(" -> [ ") + len(draft_name) + len(" ]")

    def _draw_value_pair(
        self,
        screen: curses.window,
        y: int,
        max_x: int,
        x: int,
        width: int,
        edit: RepoEdit,
        original_text: str,
        original_field: int,
        draft_text: str | None,
        draft_field: int,
        selected: bool,
        edit_cursor: int | None,
    ) -> tuple[int, int] | None:
        available = max(0, min(width, max_x - x - 1))
        if available <= 0:
            return None

        rel_x = 0
        rel_x = self._draw_field_text(
            screen,
            y,
            x,
            available,
            rel_x,
            original_text,
            self._value_attr(edit, selected, original_field, editing=False),
        )
        if draft_text is None:
            return None

        separator_attr = curses.A_DIM
        rel_x = self._draw_field_text(screen, y, x, available, rel_x, " -> [ ", separator_attr)
        draft_start = rel_x
        rel_x = self._draw_field_text(
            screen,
            y,
            x,
            available,
            rel_x,
            draft_text,
            self._value_attr(edit, selected, draft_field, editing=edit_cursor is not None),
        )
        self._draw_field_text(screen, y, x, available, rel_x, " ]", separator_attr)
        if edit_cursor is None:
            return None

        cursor_rel = min(max(0, draft_start + edit_cursor), available)
        return y, x + cursor_rel

    def _draw_single_value(
        self,
        screen: curses.window,
        y: int,
        max_x: int,
        x: int,
        width: int,
        edit: RepoEdit,
        text: str,
        field: int,
        selected: bool,
    ) -> None:
        available = max(0, min(width, max_x - x - 1))
        if available <= 0:
            return
        self._addstr(screen, y, x, text[:available], self._value_attr(edit, selected, field, editing=False))

    def _draw_dim_value(
        self,
        screen: curses.window,
        y: int,
        max_x: int,
        x: int,
        width: int,
        text: str,
        edit: RepoEdit,
    ) -> None:
        if not text:
            return
        available = max(0, min(width, max_x - x - 1))
        if available <= 0:
            return
        self._addstr(screen, y, x, text[:available], curses.A_DIM)

    def _draw_field_text(
        self,
        screen: curses.window,
        y: int,
        x: int,
        width: int,
        rel_x: int,
        text: str,
        attr: int = 0,
    ) -> int:
        visible = width - rel_x
        if visible > 0:
            self._addstr(screen, y, x + rel_x, text[:visible], attr)
        return rel_x + len(text)

    def _value_attr(self, edit: RepoEdit, selected: bool, field: int, *, editing: bool) -> int:
        if editing:
            return 0
        if selected and self.state.field == field:
            if edit.delete_active:
                return curses.color_pair(6)
            return curses.color_pair(1)
        if edit.delete_active:
            return curses.color_pair(4)
        if self._field_is_changed(edit, field):
            return curses.color_pair(3)
        return 0

    @staticmethod
    def _field_is_changed(edit: RepoEdit, field: int) -> bool:
        patch = edit.patch()
        return (
            (field == FIELD_NAME_DRAFT and "name" in patch)
            or (field == FIELD_PRIVATE and "private" in patch)
            or (field == FIELD_ARCHIVED and "archived" in patch)
        )

    def _key_matches(self, action: str, key: int) -> bool:
        key_name = self._key_name(key)
        return any(sequence == (key_name,) for sequence in self.config.keybindings.get(action, ()))

    @staticmethod
    def _key_name(key: int) -> str | None:
        if key == -1:
            return None
        if key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
            return "enter"
        if key == 27:
            return "esc"
        if key in (curses.KEY_BACKSPACE, 127, 8):
            return "backspace"
        if key == curses.KEY_DC:
            return "delete"
        if key == curses.KEY_LEFT:
            return "left"
        if key == curses.KEY_RIGHT:
            return "right"
        if key == curses.KEY_UP:
            return "up"
        if key == curses.KEY_DOWN:
            return "down"
        if key == curses.KEY_HOME:
            return "home"
        if key == curses.KEY_END:
            return "end"
        if key == curses.KEY_PPAGE:
            return "pageup"
        if key == curses.KEY_NPAGE:
            return "pagedown"
        if key == curses.KEY_BTAB:
            return "shift-tab"
        if key == 9:
            return "tab"
        if key == 4:
            return "ctrl-d"
        if key == 3:
            return "ctrl-c"
        if key == 21:
            return "ctrl-u"
        if key == ord(" "):
            return "space"
        if 0 <= key <= 255:
            return chr(key)
        return str(key)

    def _name_edit_status(self) -> str:
        submit = self._format_action_keys("name_edit_submit")
        cancel = self._format_action_keys("name_edit_cancel")
        exit_keys = self._format_action_keys("name_edit_exit")
        parts = [f"{submit} saves", f"{exit_keys} saves"]
        if cancel:
            parts.append(f"{cancel} cancels")
        return "  ".join(parts)

    def _format_action_keys(self, action: str) -> str:
        sequences = self.config.keybindings.get(action, ())
        if not sequences:
            return ""
        return " or ".join(self._format_key_sequence(sequence) for sequence in sequences)

    @staticmethod
    def _format_key_sequence(sequence: tuple[str, ...]) -> str:
        names = {
            "backspace": "Backspace",
            "ctrl-c": "Ctrl-C",
            "ctrl-d": "Ctrl-D",
            "ctrl-u": "Ctrl-U",
            "delete": "Delete",
            "down": "Down",
            "end": "End",
            "enter": "Enter",
            "esc": "Esc",
            "home": "Home",
            "left": "Left",
            "pagedown": "PgDn",
            "pageup": "PgUp",
            "right": "Right",
            "shift-tab": "Shift-Tab",
            "space": "Space",
            "tab": "Tab",
            "up": "Up",
        }
        if all(len(key) == 1 for key in sequence):
            return "".join(sequence)
        return " ".join(names.get(key, key) for key in sequence)

    def _draw_help(self, screen: curses.window) -> None:
        max_y, max_x = screen.getmaxyx()
        lines = [
            "Keys",
            f"{self._format_action_keys('move_down')}/{self._format_action_keys('move_up')}: move rows",
            f"{self._format_action_keys('move_left')}/{self._format_action_keys('move_right')}: move values",
            "i or Enter on a repo name value: edit a new name",
            "Space on original repo name: edit hidden draft or hide visible new name",
            "Space on new repo name: hide the new-name value",
            "Space or Enter on visibility/archive: toggle the value in place",
            "o on original repo name: open the repository",
            "a: toggle archive    p: toggle public/private    x: mark delete",
            "s: cycle sort by name, updated, created, commits, language, total chars",
            "r: reset current row    R: reset all rows",
            ":w: save staged edits to disk only",
            ":wq: save, review API calls, confirm, apply, then quit",
            ":q or q after :w: review API calls, confirm, apply, then quit",
            ":q! quit without applying saved or unsaved staged edits",
            f"{self._name_edit_status()} in name edit mode",
            "Esc/Ctrl-C: cancel prompts or quit when no prompt is open",
            "Press any key to close help",
        ]
        width = min(max(len(line) for line in lines) + 4, max_x - 2)
        height = min(len(lines) + 2, max_y - 2)
        top = max(0, (max_y - height) // 2)
        left = max(0, (max_x - width) // 2)
        for y in range(top, top + height):
            self._addstr(screen, y, left, " " * width, curses.A_REVERSE)
        for index, line in enumerate(lines[: height - 2]):
            attr = curses.A_REVERSE | (curses.A_BOLD if index == 0 else 0)
            self._addstr(screen, top + 1 + index, left + 2, line[: width - 4], attr)

    def _save_status_label(self) -> str:
        if not self._changed_count():
            return "clean"
        if self._current_plan_is_saved():
            return "saved"
        return "unsaved"

    def _sort_status_label(self) -> str:
        if self.state.sort_index < 0:
            return "fetched"
        return SORT_MODES[self.state.sort_index]

    @staticmethod
    def _addstr(screen: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
        try:
            screen.addstr(y, x, text, attr)
        except curses.error:
            pass
