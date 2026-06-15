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

    def test_delete_mark_is_changed_and_dominates_patch(self) -> None:
        edit = RepoEdit(repo())
        edit.start_name_insert()
        edit.set_name("new-name")

        edit.toggle_delete()

        self.assertTrue(edit.changed)
        self.assertTrue(edit.delete_active)
        self.assertEqual(edit.patch(), {})
        self.assertEqual(edit.summary(), "delete octocat/hello-world")

    def test_repository_from_api_reads_display_metadata(self) -> None:
        parsed = Repository.from_api(
            {
                "id": 1,
                "owner": {"login": "octocat"},
                "name": "hello-world",
                "private": False,
                "archived": False,
                "html_url": "https://github.com/octocat/hello-world",
                "visibility": "public",
                "created_at": "2020-01-02T03:04:05Z",
                "updated_at": "2024-05-06T07:08:09Z",
                "language": "Python",
            }
        )

        self.assertEqual(parsed.created_at, "2020-01-02T03:04:05Z")
        self.assertEqual(parsed.updated_at, "2024-05-06T07:08:09Z")
        self.assertEqual(parsed.language, "Python")


if __name__ == "__main__":
    unittest.main()
