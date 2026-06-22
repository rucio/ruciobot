"""
Abstract base class for all RucioBot checks.
"""

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from github import Github
from github.GithubException import GithubException, RateLimitExceededException
from github.PullRequest import PullRequest
from github.Repository import Repository

# PRs carrying this label are completely skipped by all bot checks.
NO_BOT_LABEL = "no-bot"

# Secondary-rate-limit backoff. When GitHub rejects a write because too many
# mutating requests were made in a short window it returns a 403 (raised by
# PyGithub as RateLimitExceededException), usually with a Retry-After header.
# We wait and retry the same PR a few times before giving up on it.
MAX_SECONDARY_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 300  # cap, so an oversized Retry-After cannot hang the job

# Logins Dependabot uses to open PRs. Dependency-update PRs are managed by
# Dependabot itself, so the bot leaves them alone: it must not mark them stale,
# nag them to rebase, or close them for failing tests.
DEPENDABOT_LOGINS = frozenset({"dependabot[bot]", "dependabot-preview[bot]"})


def is_dependabot_pr(pr) -> bool:
    """Return True if the PR was opened by Dependabot."""
    user = getattr(pr, "user", None)
    login = getattr(user, "login", None)
    return login in DEPENDABOT_LOGINS


def is_draft_pr(pr) -> bool:
    """Return True if the PR is still a draft.

    Draft PRs are explicitly marked work in progress, so nothing is owed by the
    author yet. ``PullRequest.draft`` is a plain bool on real PRs; the default
    keeps this safe for objects that do not set the attribute.
    """
    return getattr(pr, "draft", False) is True


def exclusion_reason(pr) -> str | None:
    """Return why the bot should skip *pr*, or ``None`` if it should not.

    A PR is excluded from every bot check when it was opened by Dependabot, is
    still a draft, or carries the ``no-bot`` label. The returned string is
    phrased to read naturally after the PR reference in a log line, e.g.
    ``PR #5 was opened by Dependabot``.
    """
    if is_dependabot_pr(pr):
        return "was opened by Dependabot"
    if is_draft_pr(pr):
        return "is a draft"
    if NO_BOT_LABEL in [lbl.name for lbl in pr.labels]:
        return f"has '{NO_BOT_LABEL}' label"
    return None


def count_business_days(start: datetime, end: datetime) -> int:
    """Count weekdays (Mon - Fri) between *start* and *end*, exclusive of start.

    Saturdays (weekday 5) and Sundays (weekday 6) are not counted, so
    a PR pushed on Friday afternoon will show zero weekday inactivity
    when the bot runs over the weekend.
    """
    days = 0
    current = start
    # count current, when a full day has already passed before end
    while current + timedelta(days=1) < end:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 0=Mon … 4=Fri
            days += 1
    return days


class BaseCheck(ABC):
    """
    Base class for all checks.

    Subclasses implement :meth:`process` for a single PR. This class owns the
    shared loop: it fetches the open PRs and runs ``process`` on each one,
    isolating per-PR failures so one bad PR cannot abort the whole sweep.
    """

    #: Short message printed when the check starts (overridden per check).
    summary: str = "Running check"

    def run(self, gh: Github, repo_name: str) -> None:
        print(f"{self.summary} in {repo_name}...")
        repo = gh.get_repo(repo_name)

        # Materialise the full list of open PRs *before* processing any of them.
        # The checks mutate PRs (posting comments, adding or removing labels),
        # which bumps each PR's ``updated_at``. GitHub returns ``get_pulls`` as a
        # lazily-paginated list that is re-sorted server-side on every page fetch,
        # and the "next page" link is offset-based rather than a stable cursor.
        # Mutating PRs while iterating therefore shifts later pages and silently
        # skips PRs across page boundaries. Pulling every page up front, while
        # nothing has changed yet, gives a stable snapshot; the in-memory order
        # is then irrelevant to correctness.
        pulls = list(repo.get_pulls(state="open"))

        for pr in pulls:
            keep_going = self._process_pr_safely(pr, repo)
            if not keep_going:
                break

    def _process_pr_safely(self, pr: PullRequest, repo: Repository) -> bool:
        """Run :meth:`process` on a single PR with error isolation.

        A failure on one PR (a transient API error, or a secondary rate limit
        triggered by rapid comment creation) must never abort the whole sweep.

        Returns ``True`` to continue with the next PR, or ``False`` to stop the
        run entirely. We only stop when the *primary* rate limit is exhausted,
        since no further request can succeed until it resets.
        """
        for attempt in range(MAX_SECONDARY_RATE_LIMIT_RETRIES + 1):
            try:
                self.process(pr, repo)
                return True
            except RateLimitExceededException as exc:
                wait_seconds = _secondary_rate_limit_wait(exc)
                if wait_seconds is None:
                    # Primary rate limit: nothing will succeed until it resets.
                    print("  [RATE-LIMIT] Primary rate limit reached. Stopping this run.")
                    return False
                if attempt >= MAX_SECONDARY_RATE_LIMIT_RETRIES:
                    print(
                        f"  [RATE-LIMIT] Still limited after {attempt} retries; "
                        f"skipping PR #{pr.number}."
                    )
                    return True
                print(
                    f"  [RATE-LIMIT] Secondary rate limit on PR #{pr.number}; "
                    f"waiting {wait_seconds}s before retrying."
                )
                time.sleep(wait_seconds)
            except GithubException as exc:
                print(f"  [ERROR] GitHub error on PR #{pr.number}; skipping. {exc}")
                return True
            except Exception as exc:
                print(f"  [ERROR] Unexpected error on PR #{pr.number}; skipping. {exc}")
                return True
        return True

    @abstractmethod
    def process(self, pr: PullRequest, repo: Repository) -> None:
        """Apply this check's logic to a single PR."""
        ...


def _secondary_rate_limit_wait(exc: GithubException) -> int | None:
    """Seconds to wait for a *secondary* rate-limit error.

    Returns ``None`` when *exc* is not a secondary rate limit (e.g. a primary
    rate limit), signalling that retrying the same request is pointless.
    """
    message = exc.data.get("message", "") if isinstance(exc.data, dict) else ""
    retry_after = _get_header(exc.headers, "retry-after")
    if not _is_secondary_rate_limit(str(message)) and retry_after is None:
        return None
    if retry_after is not None:
        try:
            return min(int(retry_after), MAX_RETRY_AFTER_SECONDS)
        except (TypeError, ValueError):
            pass
    return DEFAULT_RETRY_AFTER_SECONDS


def _is_secondary_rate_limit(message: str) -> bool:
    """Match GitHub's secondary-rate-limit messages (mirrors PyGithub)."""
    text = message.lower()
    return (
        text.startswith("you have exceeded a secondary rate limit")
        or text.endswith("please retry your request again later.")
        or text.endswith("please wait a few minutes before you try again.")
    )


def _get_header(headers: dict | None, name: str) -> str | None:
    """Case-insensitive header lookup."""
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None
