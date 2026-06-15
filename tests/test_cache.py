from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_repo_bulk_edit.cache import RepositoryCacheKey, load_repository_cache, save_repository_cache
from github_repo_bulk_edit.cli import _repositories_needing_metadata
from github_repo_bulk_edit.models import LanguageBreakdown, Repository


def cache_key() -> RepositoryCacheKey:
    return RepositoryCacheKey(
        api_url="https://api.github.com",
        api_version="2022-11-28",
        affiliation="owner",
        visibility="all",
        owner=None,
        limit=None,
    )


def repo(repo_id: int = 1, name: str = "hello-world") -> Repository:
    return Repository(
        id=repo_id,
        owner="octocat",
        name=name,
        private=False,
        archived=False,
        html_url=f"https://github.com/octocat/{name}",
        created_at="2020-01-02T03:04:05Z",
        updated_at="2024-05-06T07:08:09Z",
    )


class CacheTests(unittest.TestCase):
    def test_repository_cache_round_trips_repo_info_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            target = repo()
            target.languages = (LanguageBreakdown("Python", 1200),)
            target.commit_count_label = "200+"
            target.metadata_commit_pages = 2

            save_repository_cache(path, cache_key(), [target], metadata_commit_pages=2)
            result = load_repository_cache(path, cache_key())

        self.assertTrue(result.loaded)
        self.assertEqual(len(result.repositories), 1)
        loaded = result.repositories[0]
        self.assertEqual(loaded.full_name, "octocat/hello-world")
        self.assertEqual(loaded.languages[0], LanguageBreakdown("Python", 1200))
        self.assertEqual(loaded.commit_count_label, "200+")
        self.assertEqual(loaded.metadata_commit_pages, 2)

    def test_repository_cache_ignores_different_fetch_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            save_repository_cache(path, cache_key(), [repo()], metadata_commit_pages=None)

            other_key = RepositoryCacheKey(
                api_url="https://api.github.com",
                api_version="2022-11-28",
                affiliation="owner",
                visibility="private",
                owner=None,
                limit=None,
            )
            result = load_repository_cache(path, other_key)

        self.assertFalse(result.loaded)
        self.assertEqual(result.ignored_reason, "key")

    def test_only_repos_missing_requested_metadata_are_refreshed(self) -> None:
        cached = repo(1, "cached")
        cached.metadata_commit_pages = 2
        missing = repo(2, "missing")

        needing_metadata = _repositories_needing_metadata([cached, missing], 2)

        self.assertEqual(needing_metadata, [missing])


if __name__ == "__main__":
    unittest.main()
