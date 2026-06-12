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

Create a token and expose it as `GITHUB_TOKEN` or `GH_TOKEN`:

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
- You do not need `delete_repo`; this tool does not delete repositories.

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
```

By default the tool fetches repositories with `--affiliation owner`, because bulk editing collaborator repositories is usually not what you want. You can override that with GitHub's comma-separated affiliation values:

```bash
github-repo-bulk-edit --affiliation owner,organization_member
```

## Keys

Normal mode:

- `j` / `k` or arrow keys: move down/up a repository row
- `h` / `l` or arrow keys: move left/right across fields
- `g` / `G`: jump to first/last row
- `i` or `Enter` on the repo name field: enter insert mode for a new name
- `Space` on the repo name field: hide/restore the staged new-name addendum
- `Space` on the visibility field: stage public/private toggle
- `Space` on the archive field: stage archived/not archived toggle
- `r`: reset the current row
- `R`: reset every staged edit after confirmation
- `?`: show help
- `:w`: save staged edits to `.github-repo-bulk-edit-plan.json` without editing GitHub
- `:wq`: save, show the API-call summary, ask for confirmation, apply, then quit
- `:q` or `q` after `:w`: show the API-call summary, ask for confirmation, apply, then quit
- `:q!`: quit without applying saved or unsaved staged edits
- `Esc`: cancel a prompt, or quit when no prompt is open

Insert mode for repo names:

- `i` or `Enter` starts with the original repository name as the default.
- Saving a name equal to the original clears the addendum and stages no rename.
- Saving a different name shows `original -> [ new-name ]` and stages a rename.
- Pressing `Space` on the name field hides the addendum without forgetting the last staged new name.
- Pressing `Space` again restores that previous staged new name.

## Save And Apply Flow

`:w` writes the current edit state to the plan file. The default plan file is:

```text
.github-repo-bulk-edit-plan.json
```

That file contains the repositories, active staged patches, and hidden rename drafts. Hidden rename drafts are remembered so Space can restore them later, but they are not included in the patch that will be sent to GitHub.

To apply changes:

1. Run `:wq`, or run `:w` followed by `:q`.
2. Review the flat list of API calls, for example `PATCH /repos/octocat/hello-world {"name": "new-name"}`. Use PageUp/PageDown if the list is longer than the screen.
3. Type `apply`.
4. Watch the progress list. Each API call is marked as queued, started, confirmed, or errored, with a progress bar.
5. Review the final error screen if any calls failed.

If some updates fail, successful rows are cleared from the plan and the remaining staged edits are saved back to the plan file.

## What Gets Sent To GitHub

The tool uses:

- `GET /user/repos` to fetch repositories.
- `PATCH /repos/{owner}/{repo}` to apply staged changes.

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
