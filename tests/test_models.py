from __future__ import annotations

import unittest

from github_repo_bulk_edit.models import RepoEdit, Repository


def repo() -> Repository:
    return Repository(
        id=1,
        owner="octocat",
        name="hello-world",
        private=False,
        archived=False,
        html_url="https://github.com/octocat/hello-world",
    )


class RepoEditTests(unittest.TestCase):
    def test_name_insert_defaults_to_original_and_same_name_clears_change(self) -> None:
        edit = RepoEdit(repo())
        self.assertEqual(edit.start_name_insert(), "hello-world")
        self.assertTrue(edit.name_active)

        edit.set_name("hello-world")

        self.assertFalse(edit.name_active)
        self.assertEqual(edit.patch(), {})

    def test_name_toggle_preserves_previous_draft(self) -> None:
        edit = RepoEdit(repo())
        edit.start_name_insert()
        edit.set_name("new-name")

        edit.toggle_name()
        self.assertFalse(edit.name_active)
        self.assertEqual(edit.patch(), {})

        edit.toggle_name()
        self.assertTrue(edit.name_active)
        self.assertEqual(edit.name_draft, "new-name")
        self.assertEqual(edit.patch(), {"name": "new-name"})

    def test_visibility_toggle_stages_and_unstages(self) -> None:
        edit = RepoEdit(repo())

        edit.toggle_private()
        self.assertEqual(edit.patch(), {"private": True})

        edit.toggle_private()
        self.assertEqual(edit.patch(), {})

    def test_archive_toggle_stages_and_unstages(self) -> None:
        edit = RepoEdit(repo())

        edit.toggle_archived()
        self.assertEqual(edit.patch(), {"archived": True})

        edit.toggle_archived()
        self.assertEqual(edit.patch(), {})

    def test_combined_patch(self) -> None:
        edit = RepoEdit(repo())
        edit.start_name_insert()
        edit.set_name("new-name")
        edit.toggle_private()
        edit.toggle_archived()

        self.assertEqual(
            edit.patch(),
            {
                "name": "new-name",
                "private": True,
                "archived": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
