from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DOTENV_FILE = ".env"
DEFAULT_CONFIG_DIR = "github-repo-bulk-edit"
DEFAULT_CONFIG_FILE = "config.json"
GITHUB_TOKEN_ENV_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DEFAULT_KEY_SEQUENCE_TIMEOUT_MS = 500
DEFAULT_BROWSER_OPEN_COMMAND = ("firefox", "{url}")
DEFAULT_KEYBINDINGS: dict[str, tuple[str, ...]] = {
    "quit": ("q", "esc", "ctrl-c"),
    "help": ("?",),
    "command_prompt": (":",),
    "move_down": ("j", "down"),
    "move_up": ("k", "up"),
    "move_left": ("h", "left", "shift-tab"),
    "move_right": ("l", "right", "tab"),
    "first_row": ("g",),
    "last_row": ("G",),
    "activate": ("enter",),
    "edit": ("i",),
    "toggle": ("space",),
    "open_repository": ("o",),
    "mark_delete": ("x",),
    "toggle_archive": ("a",),
    "toggle_visibility": ("p",),
    "cycle_sort": ("s",),
    "reset_row": ("r",),
    "reset_all": ("R",),
    "prompt_cancel": ("esc", "ctrl-c"),
    "prompt_submit": ("enter",),
    "prompt_backspace": ("backspace",),
    "page_down": ("pagedown", "ctrl-d"),
    "page_up": ("pageup", "ctrl-u"),
    "name_edit_cancel": (),
    "name_edit_exit": ("jh",),
    "name_edit_submit": ("enter", "esc", "ctrl-c"),
    "name_edit_backspace": ("backspace",),
    "name_edit_delete": ("delete",),
    "name_edit_left": ("left",),
    "name_edit_right": ("right",),
    "name_edit_home": ("home",),
    "name_edit_end": ("end",),
}

_CONFIG_KEYS = {"browser_open_command", "key_sequence_timeout_ms", "keybindings"}
_KEY_ALIASES = {
    "backtab": "shift-tab",
    "bksp": "backspace",
    "del": "delete",
    "dn": "down",
    "escape": "esc",
    "pgdn": "pagedown",
    "pgup": "pageup",
    "return": "enter",
}
_NAMED_KEYS = {
    "backspace",
    "ctrl-d",
    "ctrl-c",
    "ctrl-u",
    "delete",
    "down",
    "end",
    "enter",
    "esc",
    "home",
    "left",
    "pagedown",
    "pageup",
    "right",
    "shift-tab",
    "space",
    "tab",
    "up",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AppConfig:
    keybindings: dict[str, tuple[tuple[str, ...], ...]]
    key_sequence_timeout_ms: int = DEFAULT_KEY_SEQUENCE_TIMEOUT_MS
    browser_open_command: tuple[str, ...] = DEFAULT_BROWSER_OPEN_COMMAND


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    return Path.home() / ".config" / DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE


def load_app_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = Path(path) if path is not None else default_config_path()
    if not config_path.is_file():
        if path is not None:
            raise ConfigError(f"Config file not found: {config_path}")
        return default_app_config()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Could not parse {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a JSON object")

    unknown = sorted(set(raw) - _CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"Unknown config key(s) in {config_path}: {', '.join(unknown)}")

    return AppConfig(
        keybindings=_parse_keybindings(raw.get("keybindings", {})),
        key_sequence_timeout_ms=_parse_timeout(raw.get("key_sequence_timeout_ms", DEFAULT_KEY_SEQUENCE_TIMEOUT_MS)),
        browser_open_command=_parse_browser_open_command(
            raw.get("browser_open_command", DEFAULT_BROWSER_OPEN_COMMAND)
        ),
    )


def default_app_config() -> AppConfig:
    return AppConfig(
        keybindings=_parse_keybindings({}),
        key_sequence_timeout_ms=DEFAULT_KEY_SEQUENCE_TIMEOUT_MS,
        browser_open_command=DEFAULT_BROWSER_OPEN_COMMAND,
    )


def parse_key_sequence(spec: str) -> tuple[str, ...]:
    stripped = spec.strip()
    if not stripped:
        raise ConfigError("Key binding entries must not be empty")

    if any(character.isspace() for character in stripped):
        return tuple(_normalize_key_name(part) for part in stripped.split())

    normalized = _normalize_key_name(stripped)
    if normalized in _NAMED_KEYS or len(normalized) == 1 or normalized.startswith("ctrl-"):
        return (normalized,)
    return tuple(_normalize_key_name(character) for character in normalized)


def _parse_keybindings(overrides: Any) -> dict[str, tuple[tuple[str, ...], ...]]:
    if not isinstance(overrides, dict):
        raise ConfigError("keybindings must be a JSON object")

    merged: dict[str, tuple[str, ...]] = dict(DEFAULT_KEYBINDINGS)
    unknown = sorted(set(overrides) - set(DEFAULT_KEYBINDINGS))
    if unknown:
        raise ConfigError(f"Unknown keybinding action(s): {', '.join(unknown)}")

    for action, value in overrides.items():
        merged[action] = _binding_specs(value)

    return {action: tuple(parse_key_sequence(spec) for spec in specs) for action, specs in merged.items()}


def _binding_specs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        if not value:
            raise ConfigError("Key binding lists must not be empty")
        return tuple(value)
    raise ConfigError("Key binding values must be a string or a list of strings")


def _normalize_key_name(name: str) -> str:
    stripped = name.strip()
    if len(stripped) == 1:
        return stripped
    lowered = stripped.lower()
    return _KEY_ALIASES.get(lowered, lowered)


def _parse_timeout(value: Any) -> int:
    if not isinstance(value, int):
        raise ConfigError("key_sequence_timeout_ms must be an integer")
    if value < 0:
        raise ConfigError("key_sequence_timeout_ms must be zero or greater")
    return value


def _parse_browser_open_command(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        command = tuple(shlex.split(value))
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        command = tuple(value)
    else:
        raise ConfigError("browser_open_command must be a string or a list of strings")

    if not command:
        raise ConfigError("browser_open_command must not be empty")
    if not any("{url}" in part for part in command):
        command = (*command, "{url}")
    return command


def get_github_token(dotenv_path: str | os.PathLike[str] = DEFAULT_DOTENV_FILE) -> str | None:
    for name in GITHUB_TOKEN_ENV_NAMES:
        token = os.environ.get(name)
        if token:
            return token

    dotenv_values = load_dotenv(dotenv_path)
    for name in GITHUB_TOKEN_ENV_NAMES:
        token = dotenv_values.get(name)
        if token:
            return token
    return None


def load_dotenv(path: str | os.PathLike[str] = DEFAULT_DOTENV_FILE) -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.is_file():
        return {}

    values: dict[str, str] = {}
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()

    key, separator, value = stripped.partition("=")
    if not separator:
        return None

    key = key.strip()
    if not _ENV_NAME_RE.match(key):
        return None

    return key, _parse_env_value(value)


def _parse_env_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]

    comment_start = stripped.find(" #")
    if comment_start != -1:
        stripped = stripped[:comment_start].rstrip()
    return stripped
