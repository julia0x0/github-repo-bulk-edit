from __future__ import annotations

import unittest
from typing import Any

from github_repo_bulk_edit.api import DEFAULT_METADATA_WORKERS, GitHubClient
from github_repo_bulk_edit.models import Repository


def repo() -> Repository:
    return Repository(
        id=1,
        owner="octocat",
        name="hello-world",
        private=False,
        archived=False,
        html_url="https://github.com/octocat/hello-world",
    )


class FakeGitHubClient(GitHubClient):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(token="token")
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, str] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.requests.append((method, path, params))
        return self.responses.pop(0)


class GitHubClientTests(unittest.TestCase):
    def test_list_repositories_progress_reports_completed_pages(self) -> None:
        client = FakeGitHubClient(
            [
                [
                    {
                        "id": 1,
                        "owner": {"login": "octocat"},
                        "name": "hello-world",
                        "private": False,
                        "archived": False,
                        "html_url": "https://github.com/octocat/hello-world",
                    }
                ]
            ]
        )
        progress: list[tuple[int, int]] = []

        repos = client.list_repositories(progress=lambda count, page: progress.append((count, page)))

        self.assertEqual([repo.name for repo in repos], ["hello-world"])
        self.assertEqual(progress, [(1, 1)])

    def test_repository_languages_sorts_by_character_count(self) -> None:
        client = FakeGitHubClient([{"Python": 1200, "Shell": 80}])

        languages = client.repository_languages(repo())

        self.assertEqual([language.name for language in languages], ["Python", "Shell"])
        self.assertEqual([language.chars for language in languages], [1200, 80])

    def test_bounded_commit_count_label_stops_after_short_page(self) -> None:
        client = FakeGitHubClient([[{}, {}, {}]])

        label = client.bounded_commit_count_label(repo(), pages=2, per_page=100)

        self.assertEqual(label, "3")
        self.assertEqual(len(client.requests), 1)

    def test_bounded_commit_count_label_marks_more_after_page_limit(self) -> None:
        client = FakeGitHubClient([[{}] * 100, [{}] * 100])

        label = client.bounded_commit_count_label(repo(), pages=2, per_page=100)

        self.assertEqual(label, "200+")
        self.assertEqual(
            [request[2] for request in client.requests],
            [{"per_page": "100", "page": "1"}, {"per_page": "100", "page": "2"}],
        )

    def test_enrich_repository_metadata_reports_repo_progress(self) -> None:
        target = repo()
        client = FakeGitHubClient([{"Python": 1200}, [{}] * 3])
        progress: list[tuple[int, int, str]] = []

        client.enrich_repository_metadata(
            [target],
            progress=lambda index, total, repository: progress.append((index, total, repository.full_name)),
        )

        self.assertEqual(progress, [(1, 1, "octocat/hello-world")])
        self.assertEqual(target.commit_count_label, "3")
        self.assertEqual(target.languages[0].name, "Python")
        self.assertEqual(target.metadata_commit_pages, 2)

    def test_metadata_workers_default_to_sixteen(self) -> None:
        self.assertEqual(DEFAULT_METADATA_WORKERS, 16)

    def test_delete_repository_sends_delete_request(self) -> None:
        client = FakeGitHubClient([None])

        client.delete_repository(repo())

        self.assertEqual(client.requests, [("DELETE", "/repos/octocat/hello-world", None)])


if __name__ == "__main__":
    unittest.main()
