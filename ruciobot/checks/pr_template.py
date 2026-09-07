"""
PR-template check: require the standard template marker on new pull requests.

Pull requests created before ``TEMPLATE_ENFORCEMENT_DATE`` are grandfathered.
For newer, ready-for-review pull requests, a missing marker is flagged
immediately and the pull request is closed after one weekday if it remains
missing. The warning comment records the start of the deadline, so unrelated
activity and manual label removal do not restart it.
"""

from datetime import UTC, datetime

from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import (
    BaseCheck,
    count_business_days,
    delete_bot_comments,
    exclusion_reason,
    latest_bot_comment,
    post_bot_comment,
)

PR_TEMPLATE_MARKER = "<!-- rucio-pr-template -->"
TEMPLATE_ENFORCEMENT_DATE = datetime(2026, 9, 8, tzinfo=UTC)
MISSING_TEMPLATE_LABEL = "missing-template"
MISSING_TEMPLATE_CLOSE_DAYS = 1

KIND_PREFIX = "missing-template-"
KIND_WARNING = "missing-template-warning"
KIND_CLOSE = "missing-template-close"

TEMPLATE_URL = "https://github.com/rucio/rucio/blob/master/.github/PULL_REQUEST_TEMPLATE.md"

WARNING_COMMENT = (
    "This PR does not appear to use the standard Rucio pull request template. "
    f"Please edit the PR description and copy the [current template]({TEMPLATE_URL}). "
    f"The PR will be closed after {MISSING_TEMPLATE_CLOSE_DAYS} weekday if the template "
    "marker remains missing."
)

CLOSE_COMMENT = (
    "Closing this PR because the standard pull request template was not added within "
    f"{MISSING_TEMPLATE_CLOSE_DAYS} weekday of the warning. Please update the PR description "
    f"using the [current template]({TEMPLATE_URL}) before reopening it."
)


class PRTemplateCheck(BaseCheck):
    """Warn and close new pull requests that omit the template marker."""

    summary = "Checking for PRs missing the standard template"

    def process(self, pr: PullRequest, repo: Repository) -> None:
        process_pr_template(pr)


def process_pr_template(pr: PullRequest) -> None:
    """Apply the template-marker warning and closure lifecycle to one PR."""
    labelled = _has_label(pr, MISSING_TEMPLATE_LABEL)
    reason = exclusion_reason(pr)
    if reason:
        print(f"  [SKIP] PR #{pr.number} {reason}. Skipping.")
        if labelled:
            _clear_missing_template(pr)
        return

    assert pr.created_at is not None, f"PR #{pr.number} has no created_at timestamp"
    created_at = _to_utc(pr.created_at)
    if created_at < TEMPLATE_ENFORCEMENT_DATE:
        if labelled:
            _clear_missing_template(pr)
        return

    if pr.draft:
        print(f"  [SKIP] PR #{pr.number} is a draft. Skipping.")
        return

    if PR_TEMPLATE_MARKER in (pr.body or ""):
        if labelled:
            _clear_missing_template(pr)
        return

    kind, comment = latest_bot_comment(pr)

    # Reopening a PR without fixing the template does not start a new grace period.
    if kind == KIND_CLOSE:
        if not labelled:
            pr.add_to_labels(MISSING_TEMPLATE_LABEL)
        _close_pr(pr)
        return

    if kind != KIND_WARNING or comment is None or comment.created_at is None:
        _warn_pr(pr)
        if not labelled:
            pr.add_to_labels(MISSING_TEMPLATE_LABEL)
        return

    # The warning comment is the persistent clock, so replacing the label manually
    # cannot reset the deadline.
    if not labelled:
        pr.add_to_labels(MISSING_TEMPLATE_LABEL)

    warned_on = _to_utc(comment.created_at)
    if count_business_days(warned_on, datetime.now(UTC)) >= MISSING_TEMPLATE_CLOSE_DAYS:
        _close_pr(pr)


def _has_label(pr: PullRequest, label: str) -> bool:
    return label in [item.name for item in pr.labels]


def _warn_pr(pr: PullRequest) -> None:
    print(f"  [WARN] PR #{pr.number} is missing the standard PR template. Warning the author.")
    post_bot_comment(pr, KIND_WARNING, WARNING_COMMENT)


def _close_pr(pr: PullRequest) -> None:
    print(f"  [CLOSE] PR #{pr.number} is still missing the standard PR template. Closing.")
    post_bot_comment(pr, KIND_CLOSE, CLOSE_COMMENT)
    pr.edit(state="closed")


def _clear_missing_template(pr: PullRequest) -> None:
    print(
        f"  [INFO] PR #{pr.number} uses the standard template. "
        f"Removing '{MISSING_TEMPLATE_LABEL}' label."
    )
    pr.remove_from_labels(MISSING_TEMPLATE_LABEL)
    delete_bot_comments(pr, KIND_PREFIX)


def _to_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
