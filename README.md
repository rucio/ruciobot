# RucioBot

RucioBot is a GitHub App that automates routine pull request maintenance for the [Rucio](https://github.com/rucio/rucio) repository. It runs on a schedule and currently handles four tasks: checking that new pull requests use the standard template, marking inactive pull requests as stale, closing pull requests that have had failing tests without activity for several days, and notifying authors of pull requests that cannot be merged due to conflicts.

The bot authenticates as a GitHub App and interacts with the GitHub API through [PyGithub](https://pygithub.readthedocs.io). It inspects open pull requests, applies labels, posts comments, and closes PRs according to configurable rules.

Responsible for PR merging in [rucio/ruciobot](https://github.com/rucio/ruciobot/): [Mayank Sharma](https://github.com/maany)

## Checks

**PR template.** Pull requests created at or after 8 September 2026 at 00:00 UTC must contain the standard template marker. Drafts are checked once marked ready for review. A missing marker receives the `missing-template` label and a warning; if it remains absent for one weekday, the pull request is closed. Adding the marker removes the label and warning. The template contents are not validated.

**Stale PRs.** The bot decides who each PR is waiting on. A PR is only marked stale, and eventually closed, when it is waiting on its *author*: a reviewer has engaged and the author has not pushed or replied since. A PR that is waiting on the *maintainers* is never closed for inactivity. That covers a PR that has never been reviewed, one with a pending review request, and one where the author has already responded to the last review. These are surfaced with a `needs-review` label once no reviewer has engaged with them for fourteen weekdays, counted from the last review or from the PR's creation. Author activity does not reset that clock, so an actively updated PR that nobody reviews is still surfaced. Approved PRs are left alone, as they are waiting on a merge.

The stale countdown starts from the latest commit or human discussion, including reviews, comments, inline replies and comment edits. Bot comments, bot reviews and metadata changes such as labels do not restart it. The default warning threshold is 14 weekdays. Once warned, a PR gets seven further weekdays from the warning or the latest relevant activity, whichever is later. A stale label without a recognised warning cannot trigger closure; the bot issues a fresh warning when the inactivity threshold is met. If activity cannot be fetched, the bot leaves the PR unchanged for that run.

**Failing tests.** A pull request with failing CI checks is warned after one weekday of inactivity. If it remains inactive and labeled for three more weekdays, it is closed.

**Needs rebase.** A pull request that has merge conflicts with its target branch receives a comment asking the author to rebase, and is labeled `needs-rebase`. If the conflicts remain and the PR sees no activity for five weekdays, the author is warned that the PR will be closed; after five further weekdays of inactivity it is closed. Once the conflicts are resolved, the label and the warning are removed automatically on the next run.

When a PR qualifies for more than one check, the most urgent one owns the closure countdown: a missing template takes precedence over failing tests, which take precedence over a needed rebase, which takes precedence over staleness. Lower-priority checks pause while `missing-template` is present. The needs-rebase check keeps its label for information but pauses its countdown while `failing-tests` is present, and the stale check lifts its labels entirely while a higher-priority check owns the PR.

The bot keeps at most one comment per pull request, so it does not dilute the discussion history: each new bot comment replaces the previous one, and closure comments carry a one-line recap of when the warning was issued. When a PR returns to a healthy state, the check's comment is removed entirely. The bot identifies its comments through a hidden marker combined with its own account login, and never deletes comments written by anyone else.

PRs opened by [Dependabot](https://docs.github.com/en/code-security/dependabot) and PRs labeled `no-bot` are excluded from all checks. More checks will be added over time. To request a new feature or report a bug, please open an issue.

## Running the bot

The bot is invoked via the `ruciobot` CLI. It requires either a GitHub App credential pair (`APP_ID` and `PRIVATE_KEY`) or a personal access token (`GITHUB_TOKEN`), and the target repository name.

```
ruciobot --action pr-template --repo rucio/rucio
ruciobot --action stale --repo rucio/rucio
ruciobot --action failing-tests --repo rucio/rucio
ruciobot --action needs-rebase --repo rucio/rucio
```

Credentials can be passed as flags or set as environment variables. See `ruciobot --help` for all options.

For Rucio-specific context, the project documentation is available at [rucio.cern.ch/documentation](https://rucio.cern.ch/documentation).

## Development

Install dependencies using [uv](https://docs.astral.sh/uv/):

```
uv sync --extra dev
```

Run tests:

```
uv run pytest tests/ -v
```

Run linters:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy ruciobot/
```

Pre-commit hooks for Ruff and mypy can be installed with `uv run pre-commit install`.
