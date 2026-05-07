# RucioBot

RucioBot is a GitHub App that automates routine pull request maintenance for the [Rucio](https://github.com/rucio/rucio) repository. It runs on a schedule and currently handles three tasks: marking inactive pull requests as stale, closing pull requests that have had failing tests without activity for several days, and notifying authors of pull requests that cannot be merged due to conflicts.

The bot authenticates as a GitHub App and interacts with the GitHub API through [PyGithub](https://pygithub.readthedocs.io). It inspects open pull requests, applies labels, posts comments, and closes PRs according to configurable rules.

Responsible for PR merging in [rucio/ruciobot](https://github.com/rucio/ruciobot/): [Mayank Sharma](https://github.com/maany)

## Checks

**Stale PRs.** A pull request is marked stale after a configurable number of weekdays without activity. If the PR has pending review requests, it is skipped. Stale PRs that remain inactive are eventually closed.

**Failing tests.** A pull request with failing CI checks is warned after one weekday of inactivity. If it remains inactive and labeled for three more weekdays, it is closed.

**Needs rebase.** A pull request that has merge conflicts with its target branch receives a comment asking the author to rebase, and is labeled `needs-rebase`. Once the conflicts are resolved, the label is removed automatically on the next run.

PRs labeled `no-bot` are excluded from all checks. More checks will be added over time. To request a new feature or report a bug, please open an issue.

## Running the bot

The bot is invoked via the `ruciobot` CLI. It requires either a GitHub App credential pair (`APP_ID` and `PRIVATE_KEY`) or a personal access token (`GITHUB_TOKEN`), and the target repository name.

```
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
