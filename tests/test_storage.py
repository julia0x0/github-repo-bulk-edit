from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_repo_bulk_edit.models import RepoEdit, Repository
from github_repo_bulk_edit.storage import load_plan, plan_signature, save_plan


def repo(repo_id: int = 1, name: str = "hello-world") -> Repository:
    return Repository(
        id=repo_id,
        owner="octocat",
        name=name,
        private=False,
        archived=False,
        html_url=f"https://github.com/octocat/{name}",
    )


class StorageTests(unittest.TestCase):
    def test_save_and_load_active_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            edit = RepoEdit(repo())
            edit.start_name_insert()
            edit.set_name("new-name")
            edit.toggle_private()

            saved = save_plan(path, [edit])

            self.assertEqual(saved, 1)
            loaded_edit = RepoEdit(repo())
            result = load_plan(path, [loaded_edit])
            self.assertEqual(result.loaded, 1)
            self.assertEqual(loaded_edit.patch(), {"name": "new-name", "private": True})

    def test_hidden_name_draft_is_persisted_without_being_a_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            edit = RepoEdit(repo())
            edit.start_name_insert()
            edit.set_name("new-name")
            edit.toggle_name()

            saved = save_plan(path, [edit])

            self.assertEqual(saved, 0)
            loaded_edit = RepoEdit(repo())
            load_plan(path, [loaded_edit])
            self.assertFalse(loaded_edit.name_active)
            self.assertEqual(loaded_edit.name_draft, "new-name")
            self.assertEqual(loaded_edit.patch(), {})

            loaded_edit.toggle_name()
            self.assertEqual(loaded_edit.patch(), {"name": "new-name"})

    def test_plan_signature_tracks_current_state(self) -> None:
        edit = RepoEdit(repo())
        clean = plan_signature([edit])

        edit.toggle_archived()

        self.assertNotEqual(clean, plan_signature([edit]))

    def test_plan_signature_is_independent_of_display_order(self) -> None:
        first = RepoEdit(repo(1, "first"))
        first.toggle_private()
        second = RepoEdit(repo(2, "second"))
        second.toggle_archived()

        self.assertEqual(plan_signature([first, second]), plan_signature([second, first]))

    def test_delete_mark_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            edit = RepoEdit(repo())
            edit.toggle_delete()

            saved = save_plan(path, [edit])

            self.assertEqual(saved, 1)
            loaded_edit = RepoEdit(repo())
            load_plan(path, [loaded_edit])
            self.assertTrue(loaded_edit.delete_active)
            self.assertTrue(loaded_edit.changed)

    def test_malformed_plan_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text("not json", encoding="utf-8")

            result = load_plan(path, [RepoEdit(repo())])

            self.assertEqual(result.loaded, 0)
            self.assertEqual(result.ignored, 1)


if __name__ == "__main__":
    unittest.main()
