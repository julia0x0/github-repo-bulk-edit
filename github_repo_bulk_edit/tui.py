from __future__ import annotations

import curses
import json
from dataclasses import dataclass
from pathlib import Path

from .api import GitHubClient, GitHubError
from .models import RepoEdit, archive_label, visibility_label
from .storage import clear_plan, plan_signature, save_plan

FIELD_NAME = 0
FIELD_PRIVATE = 1
FIELD_ARCHIVED = 2
FIELD_COUNT = 3


@dataclass(slots=True)
class ViewState:
    row: int = 0
    field: int = 0
    offset: int = 0
    status: str = ""
    help_visible: bool = False


@dataclass(slots=True)
class ApplyItem:
    edit: RepoEdit
    call: str
    status: str = "queued"
    message: str = ""


class RepoBulkEditApp:
    def __init__(
        self,
        *,
        client: GitHubClient,
        edits: list[RepoEdit],
        plan_file: Path,
        loaded_plan: bool = False,
    ) -> None:
        self.client = client
        self.edits = edits
        self.plan_file = plan_file
        self.saved_signature = plan_signature(edits) if loaded_plan else ""
        self.state = ViewState()

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

    def _handle_key(self, screen: curses.window, key: int) -> int | None:
        self.state.status = ""
        if self.state.help_visible:
            self.state.help_visible = False
            return None
        if key in (ord("q"), 27):
            return self._quit(screen, force=False)
        if key == ord("?"):
            self.state.help_visible = True
            return None
        if key == ord(":"):
            return self._command_prompt(screen)
        if key in (ord("j"), curses.KEY_DOWN):
            self._move_row(1)
        elif key in (ord("k"), curses.KEY_UP):
            self._move_row(-1)
        elif key in (ord("h"), curses.KEY_LEFT, curses.KEY_BTAB):
            self._move_field(-1)
        elif key in (ord("l"), curses.KEY_RIGHT, 9):
            self._move_field(1)
        elif key == ord("g"):
            self.state.row = 0
        elif key == ord("G"):
            self.state.row = len(self.edits) - 1
        elif key in (ord(" "), ord("\n"), curses.KEY_ENTER):
            self._activate_current_field(screen, insert_name=key != ord(" "))
        elif key == ord("i"):
            self._activate_current_field(screen, insert_name=True)
        elif key == ord("r"):
            self.edits[self.state.row].reset()
            self.state.status = f"Reset {self.edits[self.state.row].repo.full_name}"
        elif key == ord("R"):
            if self._confirm_text(screen, "Reset every staged edit? Type reset to continue: ", "reset"):
                for edit in self.edits:
                    edit.reset()
                self.state.status = "Reset every staged edit"
        else:
            self.state.status = "Unknown key. Press ? for help."
        return None

    def _move_row(self, delta: int) -> None:
        self.state.row = max(0, min(len(self.edits) - 1, self.state.row + delta))
        self._keep_selection_visible()

    def _move_field(self, delta: int) -> None:
        self.state.field = (self.state.field + delta) % FIELD_COUNT

    def _keep_selection_visible(self) -> None:
        usable_height = max(1, curses.LINES - 5)
        if self.state.row < self.state.offset:
            self.state.offset = self.state.row
        elif self.state.row >= self.state.offset + usable_height:
            self.state.offset = self.state.row - usable_height + 1

    def _activate_current_field(self, screen: curses.window, *, insert_name: bool) -> None:
        edit = self.edits[self.state.row]
        if self.state.field == FIELD_NAME:
            if insert_name:
                initial = edit.start_name_insert()
                new_name = self._edit_name(screen, initial)
                if new_name is not None:
                    edit.set_name(new_name)
                    self.state.status = f"Staged name for {edit.repo.full_name}" if edit.changed else "Name unchanged"
                return
            edit.toggle_name()
            if edit.name_active and edit.name_draft == edit.repo.name:
                self.state.status = "Name edit enabled; press i or Enter to type a new name"
            elif edit.name_active:
                self.state.status = f"Restored staged name for {edit.repo.full_name}"
            else:
                self.state.status = f"Temporarily hid staged name for {edit.repo.full_name}"
        elif self.state.field == FIELD_PRIVATE:
            edit.toggle_private()
        elif self.state.field == FIELD_ARCHIVED:
            edit.toggle_archived()

    def _edit_name(self, screen: curses.window, initial: str) -> str | None:
        buffer = list(initial)
        cursor = len(buffer)
        while True:
            self._draw(screen, edit_buffer="".join(buffer), edit_cursor=cursor)
            key = screen.getch()
            if key == 27:
                return None
            if key in (ord("\n"), curses.KEY_ENTER):
                return "".join(buffer)
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    del buffer[cursor - 1]
                    cursor -= 1
                continue
            if key == curses.KEY_DC:
                if cursor < len(buffer):
                    del buffer[cursor]
                continue
            if key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
                continue
            if key == curses.KEY_RIGHT:
                cursor = min(len(buffer), cursor + 1)
                continue
            if key == curses.KEY_HOME:
                cursor = 0
                continue
            if key == curses.KEY_END:
                cursor = len(buffer)
                continue
            if 32 <= key <= 126:
                buffer.insert(cursor, chr(key))
                cursor += 1

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

    def _run_apply_progress(self, screen: curses.window, items: list[ApplyItem]) -> list[str]:
        failures: list[str] = []
        for index, item in enumerate(items, start=1):
            item.status = "running"
            item.message = "started"
            self._draw_apply_progress(screen, items, index - 1)
            try:
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
            if key == 27:
                return None
            if key in (curses.KEY_NPAGE, 4):
                offset = min(max(0, len(items) - available), offset + available)
                continue
            if key in (curses.KEY_PPAGE, 21):
                offset = max(0, offset - available)
                continue
            if key in (ord("\n"), curses.KEY_ENTER):
                return "".join(buffer)
            if key in (curses.KEY_BACKSPACE, 127, 8):
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
            if key == 27:
                return None
            if key in (ord("\n"), curses.KEY_ENTER):
                return "".join(buffer)
            if key in (curses.KEY_BACKSPACE, 127, 8):
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
        changed = self._changed_count()
        title = f"github-repo-bulk-edit  repos:{len(self.edits)}  staged:{changed}  plan:{self._save_status_label()}"
        self._addstr(screen, 0, 0, title[: max_x - 1], curses.A_BOLD)
        header = "repo".ljust(46) + "  visibility".ljust(18) + "  archive"
        self._addstr(screen, 2, 0, header[: max_x - 1], curses.A_DIM)
        usable_height = max(1, max_y - 6)
        self._keep_selection_visible()
        visible = self.edits[self.state.offset : self.state.offset + usable_height]
        for index, edit in enumerate(visible):
            row_number = self.state.offset + index
            y = 3 + index
            selected = row_number == self.state.row
            self._draw_row(screen, y, max_x, edit, selected, edit_buffer if selected else None)
        bottom = max_y - 2
        status = prompt or self.state.status or "j/k rows  h/l fields  i/Enter edit name  Space toggle  :w save plan  :wq apply  ? help"
        self._addstr(screen, bottom, 0, status[: max_x - 1], curses.A_REVERSE if prompt else 0)
        if edit_buffer is not None:
            cursor_x = min(len("new name: ") + edit_cursor, max_x - 1)
            self._addstr(screen, max_y - 1, 0, "Enter saves, Esc cancels"[: max_x - 1], curses.A_DIM)
            try:
                screen.move(bottom, cursor_x)
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
    ) -> None:
        name_text = edit.repo.name
        if edit_buffer is not None:
            name_text = f"{edit.repo.name} -> [ {edit_buffer} ]"
        elif edit.name_active:
            name_text = f"{edit.repo.name} -> [ {edit.name_draft or edit.repo.name} ]"
        visibility_text = visibility_label(edit.repo.private)
        if edit.private_active and edit.private_draft is not None:
            visibility_text = f"{visibility_text} -> {visibility_label(edit.private_draft)}"
        archive_text = archive_label(edit.repo.archived)
        if edit.archived_active and edit.archived_draft is not None:
            archive_text = f"{archive_text} -> {archive_label(edit.archived_draft)}"
        fields = [
            (name_text, 0, 46),
            (visibility_text, 48, 18),
            (archive_text, 68, max(1, max_x - 68)),
        ]
        for field_index, (text, x, width) in enumerate(fields):
            attr = 0
            if selected and self.state.field == field_index:
                attr |= curses.color_pair(1)
            elif edit.changed:
                attr |= curses.color_pair(3)
            self._addstr(screen, y, x, text[: max(1, min(width, max_x - x - 1))], attr)

    def _draw_help(self, screen: curses.window) -> None:
        max_y, max_x = screen.getmaxyx()
        lines = [
            "Keys",
            "j/k or arrows: move rows",
            "h/l or arrows: move fields",
            "i or Enter on repo name: edit a new name, defaulting to the original",
            "Space on repo name: hide/restore the staged new-name addendum",
            "Space on visibility/archive: toggle the staged value",
            "r: reset current row    R: reset all rows",
            ":w: save staged edits to disk only",
            ":wq: save, review API calls, confirm, apply, then quit",
            ":q or q after :w: review API calls, confirm, apply, then quit",
            ":q! quit without applying saved or unsaved staged edits",
            "Esc: cancel prompts or quit when no prompt is open",
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

    @staticmethod
    def _addstr(screen: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
        try:
            screen.addstr(y, x, text, attr)
        except curses.error:
            pass
