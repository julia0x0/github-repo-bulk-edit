from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_repo_bulk_edit.api import GitHubClient, GitHubError
from github_repo_bulk_edit.config import (
    ConfigError,
    default_app_config,
    get_github_token,
    load_app_config,
    load_dotenv,
    parse_key_sequence,
)


class ConfigTests(unittest.TestCase):
    def test_load_dotenv_reads_common_env_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "GITHUB_TOKEN=github_pat_file # local token",
                        "export GH_TOKEN='gh_file'",
                        'OTHER_TOKEN="quoted value # preserved"',
                    ]
                ),
                encoding="utf-8",
            )

            values = load_dotenv(path)

        self.assertEqual(values["GITHUB_TOKEN"], "github_pat_file")
        self.assertEqual(values["GH_TOKEN"], "gh_file")
        self.assertEqual(values["OTHER_TOKEN"], "quoted value # preserved")

    def test_get_github_token_reads_dotenv_when_process_env_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GITHUB_TOKEN=github_pat_file\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                token = get_github_token(path)

        self.assertEqual(token, "github_pat_file")

    def test_get_github_token_prefers_process_env_over_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GITHUB_TOKEN=github_pat_file\n", encoding="utf-8")

            with patch.dict(os.environ, {"GITHUB_TOKEN": "github_pat_env"}, clear=True):
                token = get_github_token(path)

        self.assertEqual(token, "github_pat_env")

    def test_client_from_env_accepts_dotenv_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GH_TOKEN=gh_file\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                client = GitHubClient.from_env(dotenv_path=path)

        self.assertEqual(client.token, "gh_file")

    def test_client_from_env_error_mentions_dotenv(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(GitHubError, r"\.env"):
                GitHubClient.from_env(dotenv_path="/path/that/does/not/exist")

    def test_default_app_config_has_jh_name_edit_exit_and_firefox_open(self) -> None:
        config = default_app_config()

        self.assertIn(("j", "h"), config.keybindings["name_edit_exit"])
        self.assertIn(("esc",), config.keybindings["name_edit_submit"])
        self.assertIn(("ctrl-c",), config.keybindings["quit"])
        self.assertIn(("ctrl-c",), config.keybindings["prompt_cancel"])
        self.assertIn(("ctrl-c",), config.keybindings["name_edit_submit"])
        self.assertIn(("x",), config.keybindings["mark_delete"])
        self.assertIn(("a",), config.keybindings["toggle_archive"])
        self.assertIn(("p",), config.keybindings["toggle_visibility"])
        self.assertIn(("s",), config.keybindings["cycle_sort"])
        self.assertEqual(config.keybindings["name_edit_cancel"], ())
        self.assertEqual(config.browser_open_command, ("firefox", "{url}"))

    def test_parse_key_sequence_supports_named_keys_and_multi_key_sequences(self) -> None:
        self.assertEqual(parse_key_sequence("jh"), ("j", "h"))
        self.assertEqual(parse_key_sequence("ctrl-d"), ("ctrl-d",))
        self.assertEqual(parse_key_sequence("Shift-Tab"), ("shift-tab",))
        self.assertEqual(parse_key_sequence("G"), ("G",))

    def test_load_app_config_reads_keybindings_timeout_and_browser_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "key_sequence_timeout_ms": 750,
                        "browser_open_command": "firefox --new-tab",
                        "keybindings": {
                            "move_down": "down",
                            "name_edit_exit": "jk",
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_app_config(path)

        self.assertEqual(config.key_sequence_timeout_ms, 750)
        self.assertEqual(config.browser_open_command, ("firefox", "--new-tab", "{url}"))
        self.assertEqual(config.keybindings["move_down"], (("down",),))
        self.assertEqual(config.keybindings["name_edit_exit"], (("j", "k"),))

    def test_load_app_config_rejects_unknown_keybinding_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"keybindings": {"unknown": "x"}}), encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_app_config(path)


if __name__ == "__main__":
    unittest.main()
