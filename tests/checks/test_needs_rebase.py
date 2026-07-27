"""
Tests for the needs-rebase check.

The check flags a conflicted PR immediately, warns after
``NEEDS_REBASE_WARN_DAYS`` weekdays of inactivity, and closes after
``NEEDS_REBASE_CLOSE_DAYS`` further weekdays. All timestamps are pinned to a
concrete Monday (2026-03-09) so that ``count_business_days`` is deterministic
regardless of when the suite runs.
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from ruciobot.checks.needs_rebase import (
    NEEDS_REBASE_CLOSE_DAYS,
    NEEDS_REBASE_LABEL,
    NEEDS_REBASE_WARN_DAYS,
    REBASE_CLOSE_COMMENT,
    REBASE_COMMENT,
    REBASE_WARNING_COMMENT,
    WARNING_MARKER,
    process_needs_rebase_pr,
)

# Pinned "now": a Monday at noon UTC.
NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def business_days_before(n: int, anchor: datetime = NOW) -> datetime:
    """Return the datetime exactly *n* business days before *anchor*."""
    current = anchor
    counted = 0
    while counted < n:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            counted += 1
    return current


# Anchored timestamps.
RECENT = business_days_before(1)  # well within the escalation threshold
PAST_WARN = business_days_before(NEEDS_REBASE_WARN_DAYS + 1)
PAST_CLOSE = business_days_before(NEEDS_REBASE_CLOSE_DAYS + 1)


def _warning_comment():
    comment = MagicMock()
    comment.body = REBASE_WARNING_COMMENT
    return comment


class TestNeedsRebaseCheck(unittest.TestCase):
    def _make_pr(
        self,
        number: int,
        mergeable,
        labels: list[str] | None = None,
        updated_at: datetime = RECENT,
        comments: list | None = None,
    ):
        pr = MagicMock()
        pr.number = number
        pr.mergeable = mergeable
        pr.updated_at = updated_at
        pr.get_issue_comments.return_value = list(comments or [])

        label_mocks = []
        for lbl in labels or []:
            m = MagicMock()
            m.name = lbl
            label_mocks.append(m)
        pr.labels = label_mocks

        return pr

    def _run(self, pr, now: datetime = NOW):
        with patch("ruciobot.checks.needs_rebase.datetime") as mock_dt:
            mock_dt.now.return_value = now
            process_needs_rebase_pr(pr)

    # Happy-path: PR has conflicts, no label yet : comment + label
    def test_flags_conflicting_pr(self):
        """PR with merge conflicts (mergeable=False) should be commented on and labeled."""
        pr = self._make_pr(1, mergeable=False)
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(REBASE_COMMENT)
        pr.add_to_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    # Already labeled, recent activity : no duplicate comment / label
    def test_skips_already_labeled_pr(self):
        """A recently active PR carrying the needs-rebase label is left alone."""
        pr = self._make_pr(2, mergeable=False, labels=[NEEDS_REBASE_LABEL])
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.get_issue_comments.assert_not_called()  # gated out before the comment scan

    # Conflicts resolved : label removed
    def test_removes_label_when_conflicts_resolved(self):
        """A PR that was labeled needs-rebase but is now mergeable should have the label removed."""
        pr = self._make_pr(3, mergeable=True, labels=[NEEDS_REBASE_LABEL])
        self._run(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    # Cleanly mergeable, no label : no-op
    def test_ignores_clean_pr(self):
        """A mergeable PR with no needs-rebase label requires no action."""
        pr = self._make_pr(4, mergeable=True)
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.remove_from_labels.assert_not_called()

    # GitHub hasn't determined mergeability yet : skip
    def test_skips_when_mergeability_unknown(self):
        """PR whose mergeability is None (not yet computed) should be skipped entirely."""
        pr = self._make_pr(5, mergeable=None)
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # no-bot label : skipped regardless of merge state
    def test_skips_no_bot_pr(self):
        """PR with the no-bot label is completely skipped, even if it has conflicts."""
        from ruciobot.checks.base import NO_BOT_LABEL

        pr = self._make_pr(6, mergeable=False, labels=[NO_BOT_LABEL])
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Dependabot author : skipped regardless of merge state
    def test_skips_dependabot_pr(self):
        """PR opened by Dependabot is skipped, even if it has conflicts."""
        pr = self._make_pr(7, mergeable=False)
        pr.user.login = "dependabot[bot]"
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Escalation: warn after NEEDS_REBASE_WARN_DAYS weekdays of inactivity

    def test_warns_inactive_labeled_pr(self):
        """A labeled PR past the warn threshold with no prior warning gets the warning."""
        pr = self._make_pr(8, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=PAST_WARN)
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(REBASE_WARNING_COMMENT)
        self.assertIn(WARNING_MARKER, REBASE_WARNING_COMMENT)
        pr.edit.assert_not_called()

    def test_no_escalation_below_threshold(self):
        """A labeled PR inactive for fewer weekdays than the threshold is left alone."""
        pr = self._make_pr(
            9,
            mergeable=False,
            labels=[NEEDS_REBASE_LABEL],
            updated_at=business_days_before(NEEDS_REBASE_WARN_DAYS - 1),
        )
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    # Escalation: close after NEEDS_REBASE_CLOSE_DAYS more weekdays

    def test_closes_warned_pr(self):
        """A labeled, already-warned PR past the close threshold is closed."""
        pr = self._make_pr(
            10,
            mergeable=False,
            labels=[NEEDS_REBASE_LABEL],
            updated_at=PAST_CLOSE,
            comments=[_warning_comment()],
        )
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(REBASE_CLOSE_COMMENT)
        pr.edit.assert_called_once_with(state="closed")

    def test_comment_scan_failure_warns_instead_of_closing(self):
        """If comments cannot be fetched, the bot re-warns; it never closes unannounced."""
        pr = self._make_pr(11, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=PAST_CLOSE)
        pr.get_issue_comments.side_effect = RuntimeError("boom")
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(REBASE_WARNING_COMMENT)
        pr.edit.assert_not_called()

    # Resolution cleans up the warning so a future conflict starts fresh

    def test_resolution_deletes_warning_comment(self):
        """Resolving conflicts removes the label and deletes the warning comment."""
        warning = _warning_comment()
        other = MagicMock()
        other.body = "unrelated discussion"
        pr = self._make_pr(
            12,
            mergeable=True,
            labels=[NEEDS_REBASE_LABEL],
            comments=[other, warning],
        )
        self._run(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REBASE_LABEL)
        warning.delete.assert_called_once()
        other.delete.assert_not_called()

    def test_weekend_only_gap_takes_no_action(self):
        """A Friday-to-Sunday gap is 0 business days, so no escalation happens."""
        friday = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        sunday = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
        pr = self._make_pr(13, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=friday)
        self._run(pr, now=sunday)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
