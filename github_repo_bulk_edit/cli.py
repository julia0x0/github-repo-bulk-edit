from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .api import DEFAULT_API_URL, DEFAULT_API_VERSION, GitHubClient, GitHubError
from .models import RepoEdit
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = GitHubClient.from_env(api_url=args.api_url, api_version=args.api_version)
        repos = client.list_repositories(
            affiliation=args.affiliation,
            visibility=args.visibility,
            owner=args.owner,
            limit=args.limit,
        )
    except GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
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
        print("No repositories matched the requested filters.")
        return 0
    app = RepoBulkEditApp(client=client, edits=edits, plan_file=args.plan_file, loaded_plan=load_result.loaded > 0)
    return app.run()


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
