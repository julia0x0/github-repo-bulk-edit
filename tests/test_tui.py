from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_repo_bulk_edit.models import LanguageBreakdown, RepoEdit, Repository
from github_repo_bulk_edit.tui import (
    FIELD_ARCHIVED,
    FIELD_NAME_DRAFT,
    FIELD_NAME_ORIGINAL,
    FIELD_PRIVATE,
    RepoBulkEditApp,
)


def repo() -> Repository:
    return Repository(
        id=1,
        owner="octocat",
        name="hello-world",
        private=False,
        archived=False,
        html_url="https://github.com/octocat/hello-world",
        created_at="2020-01-02T03:04:05Z",
        updated_at="2024-05-06T07:08:09Z",
    )


def repo_with_metadata(
    repo_id: int,
    name: str,
    *,
    created_at: str,
    updated_at: str,
    commit_count_label: str,
    languages: tuple[LanguageBreakdown, ...],
) -> Repository:
    return Repository(
        id=repo_id,
        owner="octocat",
        name=name,
        private=False,
        archived=False,
        html_url=f"https://github.com/octocat/{name}",
        created_at=created_at,
        updated_at=updated_at,
        languages=languages,
        commit_count_label=commit_count_label,
    )


class FakeScreen:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, int]] = []

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.calls.append((y, x, text, attr))


class FakeInputScreen:
    def __init__(self, keys: list[int]) -> None:
        self.keys = keys
        self.timeouts: list[int] = []

    def getch(self) -> int:
        if self.keys:
            return self.keys.pop(0)
        return -1

    def timeout(self, timeout_ms: int) -> None:
        self.timeouts.append(timeout_ms)


class TuiBehaviorTests(unittest.TestCase):
    def make_app(self) -> RepoBulkEditApp:
        edit = RepoEdit(repo())
        return RepoBulkEditApp(client=object(), edits=[edit], plan_file=Path(tempfile.gettempdir()) / "plan.json")

    def make_sort_app(self) -> RepoBulkEditApp:
        edits = [
            RepoEdit(
                repo_with_metadata(
                    1,
                    "delta",
                    created_at="2020-01-01T00:00:00Z",
                    updated_at="2024-01-01T00:00:00Z",
                    commit_count_label="20",
                    languages=(LanguageBreakdown("Ruby", 100),),
                )
            ),
            RepoEdit(
                repo_with_metadata(
                    2,
                    "alpha",
                    created_at="2022-01-01T00:00:00Z",
                    updated_at="2021-01-01T00:00:00Z",
                    commit_count_label="200+",
                    languages=(LanguageBreakdown("Python", 200), LanguageBreakdown("Shell", 50)),
                )
            ),
            RepoEdit(
                repo_with_metadata(
                    3,
                    "charlie",
                    created_at="2021-01-01T00:00:00Z",
                    updated_at="2023-01-01T00:00:00Z",
                    commit_count_label="50",
                    languages=(LanguageBreakdown("Go", 500),),
                )
            ),
        ]
        return RepoBulkEditApp(client=object(), edits=edits, plan_file=Path(tempfile.gettempdir()) / "plan.json")

    def test_space_on_original_name_without_draft_moves_to_draft_and_edits(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_NAME_ORIGINAL
        app._edit_name = lambda screen, initial: "new-name"  # type: ignore[method-assign]

        app._activate_current_field(FakeScreen(), mode="toggle")

        self.assertEqual(app.edits[0].patch(), {"name": "new-name"})
        self.assertEqual(app.state.field, FIELD_NAME_DRAFT)

    def test_space_on_original_name_with_draft_hides_draft_without_moving_selection(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.start_name_insert()
        edit.set_name("new-name")
        app.state.field = FIELD_NAME_ORIGINAL

        app._activate_current_field(FakeScreen(), mode="toggle")

        self.assertFalse(edit.name_active)
        self.assertEqual(edit.patch(), {})
        self.assertEqual(app.state.field, FIELD_NAME_ORIGINAL)

    def test_enter_on_original_name_moves_to_draft_and_edits(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_NAME_ORIGINAL
        app._edit_name = lambda screen, initial: "new-name"  # type: ignore[method-assign]

        app._activate_current_field(FakeScreen(), mode="edit")

        self.assertEqual(app.edits[0].patch(), {"name": "new-name"})
        self.assertEqual(app.state.field, FIELD_NAME_DRAFT)

    def test_space_on_draft_name_hides_draft_and_moves_to_original(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.start_name_insert()
        edit.set_name("new-name")
        app.state.field = FIELD_NAME_DRAFT

        app._activate_current_field(FakeScreen(), mode="toggle")

        self.assertFalse(edit.name_active)
        self.assertEqual(edit.patch(), {})
        self.assertEqual(app.state.field, FIELD_NAME_ORIGINAL)
        self.assertIn("will not be applied", app.state.status)

    def test_visibility_and_archive_toggle_in_place(self) -> None:
        app = self.make_app()

        app.state.field = FIELD_PRIVATE
        app._activate_current_field(FakeScreen(), mode="edit")
        self.assertEqual(app.edits[0].patch(), {"private": True})
        self.assertEqual(app.state.field, FIELD_PRIVATE)

        app.state.field = FIELD_ARCHIVED
        app._activate_current_field(FakeScreen(), mode="toggle")
        self.assertEqual(app.edits[0].patch(), {"private": True, "archived": True})
        self.assertEqual(app.state.field, FIELD_ARCHIVED)

    def test_draw_row_places_cursor_on_inline_draft_name(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_NAME_DRAFT
        screen = FakeScreen()

        with patch("github_repo_bulk_edit.tui.curses.color_pair", side_effect=lambda value: value * 100):
            cursor = app._draw_row(screen, 3, 120, app.edits[0], True, "new-name", edit_cursor=3)

        self.assertEqual(cursor, (3, len("hello-world -> [ ") + 3))
        self.assertTrue(any(call[:3] == (3, len("hello-world"), " -> [ ") for call in screen.calls))
        self.assertTrue(any(call[:3] == (3, len("hello-world -> [ new-name"), " ]") for call in screen.calls))

    def test_draw_row_renders_default_draft_name_inside_brackets(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_NAME_DRAFT
        screen = FakeScreen()

        with patch("github_repo_bulk_edit.tui.curses.color_pair", side_effect=lambda value: value * 100):
            app._draw_row(screen, 3, 120, app.edits[0], True, "hello-world", edit_cursor=0)

        self.assertTrue(any(call[:3] == (3, len("hello-world -> [ "), "hello-world") for call in screen.calls))
        self.assertTrue(any(call[:3] == (3, 48, "public") for call in screen.calls))

    def test_draw_row_renders_visibility_as_single_changed_value(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.toggle_private()
        screen = FakeScreen()

        with patch("github_repo_bulk_edit.tui.curses.color_pair", side_effect=lambda value: value * 100):
            app._draw_row(screen, 3, 120, edit, False, None)

        visibility_calls = [call for call in screen.calls if call[1] == 48]
        self.assertEqual(visibility_calls, [(3, 48, "private", 300)])
        self.assertFalse(any("->" in text for _, _, text, _ in visibility_calls))

    def test_draw_row_shifts_visibility_with_visible_draft_name_width(self) -> None:
        app = self.make_app()
        screen = FakeScreen()
        draft = "new-name-that-is-longer-than-the-default-column"
        expected_visibility_x = len("hello-world -> [ " + draft + " ]") + 2

        with patch("github_repo_bulk_edit.tui.curses.color_pair", side_effect=lambda value: value * 100):
            app._draw_row(screen, 3, 140, app.edits[0], True, draft, edit_cursor=len(draft))

        self.assertTrue(any(call[:3] == (3, expected_visibility_x, "public") for call in screen.calls))

    def test_name_edit_exit_sequence_times_out_to_literal_key(self) -> None:
        app = self.make_app()
        screen = FakeInputScreen([])

        matched, replay_keys = app._consume_key_sequence(screen, ord("j"), "name_edit_exit")

        self.assertFalse(matched)
        self.assertEqual(replay_keys, [ord("j")])
        self.assertEqual(screen.timeouts, [app.config.key_sequence_timeout_ms, -1])

    def test_name_edit_exit_sequence_matches_jh(self) -> None:
        app = self.make_app()
        screen = FakeInputScreen([ord("h")])

        matched, replay_keys = app._consume_key_sequence(screen, ord("j"), "name_edit_exit")

        self.assertTrue(matched)
        self.assertEqual(replay_keys, [])

    def test_escape_saves_in_name_edit_mode(self) -> None:
        app = self.make_app()
        buffer = list("new-name")

        action, cursor = app._apply_name_edit_key(27, buffer, len(buffer))

        self.assertEqual(action, "submit")
        self.assertEqual(cursor, len(buffer))

    def test_control_c_saves_in_name_edit_mode(self) -> None:
        app = self.make_app()
        buffer = list("new-name")

        action, cursor = app._apply_name_edit_key(3, buffer, len(buffer))

        self.assertEqual(action, "submit")
        self.assertEqual(cursor, len(buffer))

    def test_language_columns_show_first_two_languages_compactly(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.repo.languages = (
            LanguageBreakdown("Python", 8000),
            LanguageBreakdown("Shell", 2000),
            LanguageBreakdown("JavaScript", 1000),
        )

        primary = app._language_column_text(edit, 0)
        secondary = app._language_column_text(edit, 1)
        hidden = app._language_column_text(edit, 2)

        self.assertEqual(primary, "Python      73%    8kc  ~100loc")
        self.assertEqual(secondary, "Shell       18%    2kc   ~25loc")
        self.assertEqual(hidden, "")

    def test_draw_row_renders_static_metadata_columns_dimmed(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.repo.commit_count_label = "200+"
        screen = FakeScreen()

        app._draw_row(screen, 3, 140, edit, False, None)

        self.assertTrue(any(call[:3] == (3, 84, "2020-01-02") for call in screen.calls))
        self.assertTrue(any(call[:3] == (3, 98, "2024-05-06") for call in screen.calls))
        self.assertTrue(any(call[:3] == (3, 112, "200+") for call in screen.calls))

    def test_draw_row_renders_language_columns_on_main_row(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.repo.languages = (
            LanguageBreakdown("Python", 8000),
            LanguageBreakdown("Shell", 2000),
        )
        screen = FakeScreen()

        app._draw_row(screen, 3, 200, edit, False, None)

        self.assertTrue(any(call[:3] == (3, 124, "Python      80%    8kc  ~100loc") for call in screen.calls))
        self.assertTrue(any(call[:3] == (3, 157, "Shell       20%    2kc   ~25loc") for call in screen.calls))

    def test_draw_header_renders_language_titles(self) -> None:
        app = self.make_app()
        screen = FakeScreen()

        app._draw_header(screen, 200, None)

        self.assertTrue(any(call[:3] == (2, 124, "primary lang") for call in screen.calls))
        self.assertTrue(any(call[:3] == (2, 157, "secondary lang") for call in screen.calls))

    def test_visible_repo_capacity_counts_repo_rows_not_metadata_rows(self) -> None:
        self.assertEqual(RepoBulkEditApp._visible_repo_capacity(16), 10)

    def test_x_on_repo_name_toggles_delete_mark(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_NAME_ORIGINAL

        app._toggle_selected_delete()

        self.assertTrue(app.edits[0].delete_active)
        self.assertIn("Marked", app.state.status)

        app._toggle_selected_delete()

        self.assertFalse(app.edits[0].delete_active)

    def test_x_toggles_delete_mark_from_any_field_on_row(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_PRIVATE

        app._toggle_selected_delete()

        self.assertTrue(app.edits[0].delete_active)
        self.assertIn("Marked", app.state.status)

    def test_archive_and_visibility_shortcuts_toggle_from_any_field_on_row(self) -> None:
        app = self.make_app()
        app.state.field = FIELD_NAME_ORIGINAL

        app._handle_key(FakeScreen(), ord("a"))
        app.state.field = FIELD_ARCHIVED
        app._handle_key(FakeScreen(), ord("p"))

        self.assertEqual(app.edits[0].patch(), {"private": True, "archived": True})

    def test_s_cycles_sort_modes_and_preserves_selected_repo(self) -> None:
        app = self.make_sort_app()
        selected = app.edits[0]

        app._handle_key(FakeScreen(), ord("s"))
        self.assertEqual([edit.repo.name for edit in app.edits], ["alpha", "charlie", "delta"])
        self.assertIs(app.edits[app.state.row], selected)

        app._handle_key(FakeScreen(), ord("s"))
        self.assertEqual([edit.repo.name for edit in app.edits], ["delta", "charlie", "alpha"])

        app._handle_key(FakeScreen(), ord("s"))
        self.assertEqual([edit.repo.name for edit in app.edits], ["alpha", "charlie", "delta"])

        app._handle_key(FakeScreen(), ord("s"))
        self.assertEqual([edit.repo.name for edit in app.edits], ["alpha", "charlie", "delta"])

        app._handle_key(FakeScreen(), ord("s"))
        self.assertEqual([edit.repo.name for edit in app.edits], ["charlie", "alpha", "delta"])

        app._handle_key(FakeScreen(), ord("s"))
        self.assertEqual([edit.repo.name for edit in app.edits], ["charlie", "alpha", "delta"])
        self.assertEqual(app.state.status, "Sorted by total chars")

    def test_delete_mark_colors_row_red(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.toggle_delete()
        screen = FakeScreen()

        with patch("github_repo_bulk_edit.tui.curses.color_pair", side_effect=lambda value: value * 100):
            app._draw_row(screen, 3, 200, edit, False, None)

        self.assertTrue(any(call[:3] == (3, 0, "hello-world") and call[3] == 400 for call in screen.calls))
        self.assertFalse(any(call[:3] == (3, 84, "2020-01-02") and call[3] == 400 for call in screen.calls))

    def test_delete_summary_uses_delete_api_call(self) -> None:
        app = self.make_app()
        edit = app.edits[0]
        edit.toggle_delete()

        self.assertEqual(app._api_call_summary(edit), "DELETE /repos/octocat/hello-world")


if __name__ == "__main__":
    unittest.main()
