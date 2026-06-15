# github-repo-bulk-edit

A terminal UI for bulk editing GitHub repository names, public/private visibility, and archived state.

The interface stages edits locally first. `:w` saves the staged plan to disk only. Nothing is sent to GitHub until an apply flow shows the API-call summary and you type `apply`.

## Install

This project has no runtime third-party dependencies.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

You can also run it without installing the console script:

```bash
python -m github_repo_bulk_edit
```

## GitHub Token

Create a token and expose it as `GITHUB_TOKEN` or `GH_TOKEN`.

The simplest local setup is a `.env` file in the directory where you run the command:

```bash
GITHUB_TOKEN=github_pat_...
```

`.env` is ignored by git. Shell environment variables also work and take precedence over `.env`:

```bash
export GITHUB_TOKEN=github_pat_...
```

Recommended token type: a fine-grained personal access token.

Required repository permissions:

- `Metadata`: read
- `Administration`: read and write

Repository access:

- Select the repositories you want to edit, or choose all repositories if you are overhauling the whole account.
- For organization repositories, the org must allow the token to access those repositories.
- Visibility changes can be blocked by organization policy. GitHub returns a `422` error when a non-owner tries to change visibility in an organization that restricts that action.

Classic personal access token fallback:

- Use the `repo` scope if you need to include private repositories.
- `public_repo` is only appropriate for public repository access and is not enough for a full public/private overhaul.
- `delete_repo` is required only if you will use `x` to mark repositories for deletion.

GitHub docs used for these permissions:

- [List repositories for the authenticated user](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#list-repositories-for-the-authenticated-user)
- [Update a repository](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#update-a-repository)
- [Classic token scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)

## Usage

```bash
github-repo-bulk-edit
```

Common filters:

```bash
github-repo-bulk-edit --owner your-login
github-repo-bulk-edit --visibility public
github-repo-bulk-edit --limit 20
github-repo-bulk-edit --no-tui
github-repo-bulk-edit --plan-file my-overhaul-plan.json
github-repo-bulk-edit --config-file config.json
github-repo-bulk-edit --no-metadata
github-repo-bulk-edit --metadata-workers 8
github-repo-bulk-edit --refresh-cache
github-repo-bulk-edit --cache-file repo-cache.json
```

By default the tool fetches repositories with `--affiliation owner`, because bulk editing collaborator repositories is usually not what you want. You can override that with GitHub's comma-separated affiliation values:

```bash
github-repo-bulk-edit --affiliation owner,organization_member
```

## Keys

Normal mode:

- `j` / `k` or arrow keys: move down/up a repository row
- `h` / `l` or arrow keys: move left/right across selectable values
- `g` / `G`: jump to first/last row
- `i` or `Enter` on the original repo name: show the new-name value, move to it, and enter edit mode
- `i` or `Enter` on the new repo name: enter edit mode
- `Space` on the original repo name without a visible new-name value: show the new-name value, move to it, and enter edit mode
- `Space` on the original repo name with a visible new-name value: hide the new-name value while keeping selection on the original name
- `Space` on the new repo name: hide the new-name value and move selection back to the original name
- `Space` or `Enter` on the visibility field: stage/unstage the public/private toggle in place
- `Space` or `Enter` on the archive field: stage/unstage the archived/not archived toggle in place
- `p`: stage/unstage the public/private toggle anywhere on the row
- `a`: stage/unstage the archived/not archived toggle anywhere on the row
- `o` on the original repo name: open the repository in the configured browser
- `x`: mark/unmark the repository for deletion anywhere on the row; the full-brightness row values are red
- `s`: cycle sorting by name, updated date, created date, commit count, primary language, and total language characters
- `r`: reset the current row
- `R`: reset every staged edit after confirmation
- `?`: show help
- `:w`: save staged edits to `.github-repo-bulk-edit-plan.json` without editing GitHub
- `:wq`: save, show the API-call summary, ask for confirmation, apply, then quit
- `:q` or `q` after `:w`: show the API-call summary, ask for confirmation, apply, then quit
- `:q!`: quit without applying saved or unsaved staged edits
- `Esc` or `Ctrl-C`: cancel a prompt, or quit when no prompt is open

Each repository row includes dim, non-selectable metadata columns. `j` and `k` move only between repository rows.

Metadata includes:

- Created and last-updated dates from the repository list response.
- A bounded commit count label from up to two commit-list pages, shown as an exact count when below the cap or `200+` at the default cap.
- Primary and secondary language columns from GitHub's language byte counts, shown compactly as percentage, character count, and estimated lines using 80 characters per line.

Repository info and metadata are cached by default in `.github-repo-bulk-edit-cache.json`. Use `--refresh-cache` to bypass the cache, fetch fresh data, and rewrite it. If cached metadata is missing for only some repositories, the tool fetches metadata only for those repositories and updates the cache.

Use `--no-metadata` to skip the per-repository language and commit-count calls. Use `--metadata-commit-pages 0`, `1`, or `2` to control the commit-count cap. Metadata loads in parallel with 16 workers by default; use `--metadata-workers` to lower or raise that.

When opening the TUI, startup progress is printed to stderr while repositories and metadata are loading.

Insert mode for repo names:

- `i` or `Enter` starts with the original repository name, or the remembered hidden draft if one exists.
- `Enter`, `Esc`, `Ctrl-C`, or `jh` exits insert mode and stages the current text. To type `jh`, wait past the key-sequence timeout after `j`.
- Saving a name equal to the original clears the addendum and stages no rename.
- Saving a different name shows `original -> [ new-name ]` and stages a rename.
- Hiding the new-name value keeps the draft remembered, but it is not included in the GitHub patch unless restored.

## Config

By default the TUI reads JSON config from:

```text
~/.config/github-repo-bulk-edit/config.json
```

If `XDG_CONFIG_HOME` is set, the default path is `$XDG_CONFIG_HOME/github-repo-bulk-edit/config.json`.

Example:

```json
{
  "key_sequence_timeout_ms": 500,
  "browser_open_command": ["firefox", "--new-tab", "{url}"],
  "keybindings": {
    "move_down": ["j", "down"],
    "name_edit_exit": "jh",
    "open_repository": "o"
  }
}
```

`browser_open_command` can be a shell-style string or a JSON list. If no `{url}` placeholder is present, the repository URL is appended.

Keybinding values can be a string or a list of strings. Multi-key strings such as `jh` are treated as sequences; named keys include `enter`, `esc`, `space`, `tab`, `shift-tab`, arrows, `pageup`, `pagedown`, `home`, `end`, `backspace`, `delete`, `ctrl-c`, `ctrl-d`, and `ctrl-u`.

Supported keybinding actions are:

```text
quit, help, command_prompt, move_down, move_up, move_left, move_right,
first_row, last_row, activate, edit, toggle, open_repository, mark_delete,
toggle_archive, toggle_visibility, cycle_sort, reset_row, reset_all, prompt_cancel,
prompt_submit, prompt_backspace, page_down, page_up, name_edit_cancel,
name_edit_exit, name_edit_submit,
name_edit_backspace, name_edit_delete, name_edit_left, name_edit_right,
name_edit_home, name_edit_end
```

Configuring an action replaces that action's default binding list.

## Save And Apply Flow

`:w` writes the current edit state to the plan file. The default plan file is:

```text
.github-repo-bulk-edit-plan.json
```

That file contains the repositories, active staged patches, and hidden rename drafts. Hidden rename drafts are remembered so Space can restore them later, but they are not included in the patch that will be sent to GitHub.

To apply changes:

1. Run `:wq`, or run `:w` followed by `:q`.
2. Review the flat list of API calls, for example `PATCH /repos/octocat/hello-world {"name": "new-name"}` or `DELETE /repos/octocat/old-repo`. Use PageUp/PageDown if the list is longer than the screen.
3. Type `apply`.
4. Watch the progress list. Each API call is marked as queued, started, confirmed, or errored, with a progress bar.
5. Review the final error screen if any calls failed.

If some updates fail, successful rows are cleared from the plan and the remaining staged edits are saved back to the plan file.

## What Gets Sent To GitHub

The tool uses:

- `GET /user/repos` to fetch repositories.
- `GET /repos/{owner}/{repo}/languages` to fetch language byte counts when metadata is enabled.
- `GET /repos/{owner}/{repo}/commits` to fetch a bounded commit-count label when metadata is enabled.
- `PATCH /repos/{owner}/{repo}` to apply staged changes.
- `DELETE /repos/{owner}/{repo}` to delete repositories marked with `x`.

Only changed fields are sent. A combined staged edit can send:

```json
{
  "name": "new-repo-name",
  "private": true,
  "archived": true
}
```

## Notes

- The tool renames repositories but does not transfer ownership.
- GitHub redirects old repository URLs after a rename, but local git remotes are not edited by this tool.
- Archive toggling supports both archive and unarchive.
- Enterprise `internal` visibility is not modeled; this tool is for public/private repository overhauls.
