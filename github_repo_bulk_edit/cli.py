from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .api import DEFAULT_API_URL, DEFAULT_API_VERSION, DEFAULT_METADATA_WORKERS, GitHubClient, GitHubError
from .cache import DEFAULT_CACHE_FILE, RepositoryCacheKey, load_repository_cache, save_repository_cache
from .config import ConfigError, default_config_path, load_app_config
from .models import RepoEdit, Repository
from .storage import DEFAULT_PLAN_FILE, load_plan
from .tui import RepoBulkEditApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-repo-bulk-edit",
        description="Edit GitHub repository names, visibility, and archive state from a vim-like TUI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--owner",
        help="Only show repositories owned by this user or organization login.",
    )
    parser.add_argument(
        "--affiliation",
        default="owner",
        help="GitHub repository affiliation filter. Default: owner.",
    )
    parser.add_argument(
        "--visibility",
        choices=("all", "public", "private"),
        default="all",
        help="Fetch all, public, or private repositories. Default: all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Fetch at most this many repositories. Useful when testing the interface.",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"GitHub API base URL. Default: {DEFAULT_API_URL}.",
    )
    parser.add_argument(
        "--api-version",
        default=DEFAULT_API_VERSION,
        help=f"GitHub REST API version header. Default: {DEFAULT_API_VERSION}.",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Fetch repositories and print a plain table instead of opening the TUI.",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=Path(DEFAULT_PLAN_FILE),
        help=f"File used for persistent staged edits. Default: {DEFAULT_PLAN_FILE}.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        help=f"JSON config file for keybindings and browser command. Default: {default_config_path()}.",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=Path(DEFAULT_CACHE_FILE),
        help=f"File used for cached repository info and metadata. Default: {DEFAULT_CACHE_FILE}.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Bypass the repository cache, fetch fresh data, and rewrite the cache.",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Skip per-repository metadata calls for languages and bounded commit counts.",
    )
    parser.add_argument(
        "--metadata-commit-pages",
        type=int,
        choices=(0, 1, 2),
        default=2,
        help="Commit pages to inspect per repository for the commit-count label. Default: 2.",
    )
    parser.add_argument(
        "--metadata-workers",
        type=int,
        default=DEFAULT_METADATA_WORKERS,
        help=f"Parallel repository metadata requests. Default: {DEFAULT_METADATA_WORKERS}.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    progress = ProgressReporter(enabled=not args.no_tui)
    cache_key = RepositoryCacheKey(
        api_url=args.api_url,
        api_version=args.api_version,
        affiliation=args.affiliation,
        visibility=args.visibility,
        owner=args.owner,
        limit=args.limit,
    )
    try:
        client = GitHubClient.from_env(api_url=args.api_url, api_version=args.api_version)
        loaded_from_cache = False
        cache_result = load_repository_cache(args.cache_file, cache_key)
        if cache_result.loaded and not args.refresh_cache:
            repos = cache_result.repositories
            loaded_from_cache = True
            progress.message(f"Loaded {len(repos)} repositories from {args.cache_file}.")
        else:
            progress.message("Loading repositories...")
            repos = client.list_repositories(
                affiliation=args.affiliation,
                visibility=args.visibility,
                owner=args.owner,
                limit=args.limit,
                progress=progress.repo_page,
            )
            progress.message(f"Loaded {len(repos)} repositories.")

        repos_needing_metadata = (
            [] if args.no_metadata or args.no_tui else _repositories_needing_metadata(repos, args.metadata_commit_pages)
        )
        metadata_refreshed = False
        if repos_needing_metadata:
            progress.message(
                f"Loading repository metadata for {len(repos_needing_metadata)}/{len(repos)} repos "
                f"with {args.metadata_workers} workers..."
            )
            client.enrich_repository_metadata(
                repos_needing_metadata,
                commit_pages=args.metadata_commit_pages,
                workers=args.metadata_workers,
                progress=progress.metadata_repo,
            )
            metadata_refreshed = True

        if not loaded_from_cache or metadata_refreshed:
            save_repository_cache(
                args.cache_file,
                cache_key,
                repos,
                metadata_commit_pages=_common_metadata_commit_pages(repos),
            )
    except GitHubError as exc:
        progress.finish()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        progress.finish()
        print(f"error: could not write repository cache {args.cache_file}: {exc}", file=sys.stderr)
        return 1

    edits = [RepoEdit(repo) for repo in repos]
    load_result = load_plan(args.plan_file, edits)
    if args.no_tui:
        print_plain_table(edits)
        if load_result.loaded:
            print(f"\nLoaded {load_result.loaded} saved edit state(s) from {args.plan_file}.")
        if load_result.ignored:
            print(f"Ignored {load_result.ignored} saved edit state(s) that no longer match fetched repos.")
        return 0
    if not edits:
        progress.finish()
        print("No repositories matched the requested filters.")
        return 0
    try:
        app_config = load_app_config(args.config_file)
    except ConfigError as exc:
        progress.finish()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    progress.finish()
    app = RepoBulkEditApp(
        client=client,
        edits=edits,
        plan_file=args.plan_file,
        loaded_plan=load_result.loaded > 0,
        config=app_config,
        cache_file=args.cache_file,
    )
    return app.run()


def _repositories_needing_metadata(repos: list[Repository], metadata_commit_pages: int) -> list[Repository]:
    return [repo for repo in repos if getattr(repo, "metadata_commit_pages", None) != metadata_commit_pages]


def _common_metadata_commit_pages(repos: list[Repository]) -> int | None:
    pages = {getattr(repo, "metadata_commit_pages", None) for repo in repos}
    if len(pages) != 1:
        return None
    value = next(iter(pages), None)
    return value if isinstance(value, int) else None


class ProgressReporter:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._last_length = 0

    def message(self, text: str) -> None:
        if not self.enabled:
            return
        self._write(text)

    def repo_page(self, count: int, page: int) -> None:
        if not self.enabled:
            return
        self._write(f"Loading repositories... {count} fetched after page {page}")

    def metadata_repo(self, index: int, total: int, repo: object) -> None:
        if not self.enabled:
            return
        full_name = getattr(repo, "full_name", str(repo))
        self._write(f"Loading repository metadata... {index}/{total} {full_name}")

    def finish(self) -> None:
        if not self.enabled or self._last_length == 0:
            return
        sys.stderr.write("\r" + " " * self._last_length + "\r")
        sys.stderr.flush()
        self._last_length = 0

    def _write(self, text: str) -> None:
        padding = max(0, self._last_length - len(text))
        sys.stderr.write("\r" + text + " " * padding)
        sys.stderr.flush()
        self._last_length = len(text)


def print_plain_table(edits: list[RepoEdit]) -> None:
    headers = ("repository", "visibility", "archive")
    rows = [
        (
            edit.repo.full_name,
            "private" if edit.repo.private else "public",
            "archived" if edit.repo.archived else "not archived",
        )
        for edit in edits
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
