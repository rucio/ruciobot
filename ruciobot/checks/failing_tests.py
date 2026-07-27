"""
Failing-tests check: warn after WARN_DAYS of inactivity, close after CLOSE_DAYS more.

This check takes precedence over the needs-rebase and stale checks: a PR
with failing tests is escalated here even if it also needs a rebase, since
red CI is the most urgent signal and often implies a rebase is needed anyway.
"""

from datetime import UTC, datetime

from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import BaseCheck, count_business_days, exclusion_reason

FAILING_TESTS_LABEL = "failing-tests"
FAILING_TESTS_WARN_DAYS = 1  # Days of inactivity before warning
FAILING_TESTS_CLOSE_DAYS = 3  # Days of inactivity (after warning) before closing


class FailingTestsCheck(BaseCheck):
    """Warns and closes PRs that have failing CI checks and remain inactive."""

    summary = "Checking for PRs with failing tests"

    def process(self, pr: PullRequest, repo: Repository) -> None:
        process_failing_test_pr(pr, repo)


# Helpers


def process_failing_test_pr(pr: PullRequest, repo) -> None:
    """Process a single PR to check for failing tests and apply warn/close logic."""
    reason = exclusion_reason(pr)
    if reason:
        print(f"  [SKIP] PR #{pr.number} {reason}. Skipping.")
        return
    now = datetime.now(UTC)
    assert pr.updated_at is not None, f"PR #{pr.number} has no updated_at timestamp"
    last_updated = pr.updated_at.replace(tzinfo=UTC)
    inactive_days = count_business_days(last_updated, now)

    if _is_labeled_failing_tests(pr):
        if not has_failing_tests(pr, repo):
            _clear_failing_tests_label(pr)
        elif inactive_days >= FAILING_TESTS_CLOSE_DAYS:
            _close_failing_test_pr(pr)
    else:
        if inactive_days >= FAILING_TESTS_WARN_DAYS and has_failing_tests(pr, repo):
            _warn_failing_test_pr(pr)


def has_failing_tests(pr: PullRequest, repo) -> bool:
    """Returns True if any check run on the PR's head commit has conclusion 'failure'."""
    try:
        commit = repo.get_commit(pr.head.sha)
        for run in commit.get_check_runs():
            if run.conclusion == "failure":
                return True
    except Exception as e:
        print(f"  [WARN] Could not fetch check runs for PR #{pr.number}: {e}")
    return False


def _is_labeled_failing_tests(pr: PullRequest) -> bool:
    return FAILING_TESTS_LABEL in [lbl.name for lbl in pr.labels]


def _clear_failing_tests_label(pr: PullRequest) -> None:
    print(
        f"  [INFO] PR #{pr.number} tests are now passing. Removing '{FAILING_TESTS_LABEL}' label."
    )
    pr.remove_from_labels(FAILING_TESTS_LABEL)


def _warn_failing_test_pr(pr: PullRequest) -> None:
    print(
        f"  [WARN] PR #{pr.number} has failing tests and has been "
        f"inactive for {FAILING_TESTS_WARN_DAYS}+ weekday(s)."
    )
    pr.create_issue_comment(
        f"This PR has failing CI checks and has had no activity for "
        f"{FAILING_TESTS_WARN_DAYS} weekday(s). "
        f"It has been labeled **failing-tests** and will be closed in "
        f"{FAILING_TESTS_CLOSE_DAYS} weekdays unless the tests are fixed "
        f"or new activity is recorded."
    )
    pr.add_to_labels(FAILING_TESTS_LABEL)


def _close_failing_test_pr(pr: PullRequest) -> None:
    print(
        f"  [CLOSE] PR #{pr.number} has had failing tests and been inactive for too long. Closing."
    )
    pr.create_issue_comment(
        f"Closing this PR because it has had failing CI checks for more than "
        f"{FAILING_TESTS_CLOSE_DAYS} weekdays with no activity. "
        "Feel free to reopen it once the tests are fixed. "
        "If you believe this action was a mistake, please reach out to a member of the "
        "[Rucio review team](https://rucio.github.io/documentation/component_leads) "
        "with an explanation."
    )
    pr.edit(state="closed")
