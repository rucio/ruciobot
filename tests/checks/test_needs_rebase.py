"""
Tests for the needs-rebase check.

The check flags a conflicted PR immediately, warns after
``NEEDS_REBASE_WARN_DAYS`` weekdays of inactivity, and closes after
``NEEDS_REBASE_CLOSE_DAYS`` further weekdays. The bot keeps a single comment
per PR whose kind marker doubles as the escalation state. All timestamps are
pinned to a concrete Monday (2026-03-09) so that ``count_business_days`` is
deterministic regardless of when the suite runs.
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from github.GithubException import RateLimitExceededException

from ruciobot.checks.base import bot_marker, set_bot_login
from ruciobot.checks.failing_tests import FAILING_TESTS_LABEL
from ruciobot.checks.failing_tests import KIND_WARNING as FAILING_KIND_WARNING
from ruciobot.checks.needs_rebase import (
    KIND_CLOSE,
    KIND_FLAG,
    KIND_WARNING,
    MERGEABLE_POLL_ATTEMPTS,
    NEEDS_REBASE_CLOSE_DAYS,
    NEEDS_REBASE_LABEL,
    NEEDS_REBASE_WARN_DAYS,
    REBASE_COMMENT,
    REBASE_WARNING_COMMENT,
    process_needs_rebase_pr,
    rebase_close_comment,
)

# Pinned "now": a Monday at noon UTC.
NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)

BOT_LOGIN = "ruciobot[bot]"


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


def _bot_comment(kind, created_at: datetime = PAST_WARN, login: str = BOT_LOGIN):
    comment = MagicMock()
    comment.body = f"{bot_marker(kind)}\nsome text"
    comment.user = MagicMock()
    comment.user.login = login
    comment.created_at = created_at
    return comment


class TestNeedsRebaseCheck(unittest.TestCase):
    def setUp(self):
        set_bot_login(BOT_LOGIN)

    def tearDown(self):
        set_bot_login(None)

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
        with (
            patch("ruciobot.checks.needs_rebase.datetime") as mock_dt,
            patch("ruciobot.checks.needs_rebase.time.sleep"),
        ):
            mock_dt.now.return_value = now
            process_needs_rebase_pr(pr)

    # Happy-path: PR has conflicts, no label yet : comment + label
    def test_flags_conflicting_pr(self):
        """PR with merge conflicts (mergeable=False) should be commented on and labeled."""
        pr = self._make_pr(1, mergeable=False)
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_FLAG)}\n{REBASE_COMMENT}"
        )
        pr.add_to_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    # Already labeled, recent activity : no duplicate comment / label
    def test_skips_already_labeled_pr(self):
        """A recently active PR carrying the needs-rebase label is left alone."""
        pr = self._make_pr(2, mergeable=False, labels=[NEEDS_REBASE_LABEL])
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.get_issue_comments.assert_not_called()  # gated out before the comment scan

    # Conflicts resolved : label removed and own comment deleted
    def test_resolution_removes_label_and_comment(self):
        """Resolving conflicts removes the label and this check's bot comment."""
        flag = _bot_comment(KIND_FLAG)
        pr = self._make_pr(3, mergeable=True, labels=[NEEDS_REBASE_LABEL], comments=[flag])
        self._run(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REBASE_LABEL)
        flag.delete.assert_called_once()

    def test_resolution_keeps_other_checks_comments(self):
        """Cleanup on resolution is scoped to this check's own comment kinds."""
        failing_warning = _bot_comment(FAILING_KIND_WARNING)
        pr = self._make_pr(
            4, mergeable=True, labels=[NEEDS_REBASE_LABEL], comments=[failing_warning]
        )
        self._run(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REBASE_LABEL)
        failing_warning.delete.assert_not_called()

    # Cleanly mergeable, no label : no-op
    def test_ignores_clean_pr(self):
        """A mergeable PR with no needs-rebase label requires no action."""
        pr = self._make_pr(5, mergeable=True)
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.remove_from_labels.assert_not_called()

    # GitHub hasn't determined mergeability yet : poll, then skip
    def test_skips_when_mergeability_stays_unknown(self):
        """A PR whose mergeability never resolves is polled, then skipped entirely."""
        pr = self._make_pr(6, mergeable=None)
        self._run(pr)
        self.assertEqual(pr.update.call_count, MERGEABLE_POLL_ATTEMPTS)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    def test_polls_until_mergeability_resolves(self):
        """A PR whose mergeability resolves to conflicting during polling is flagged."""
        pr = self._make_pr(19, mergeable=None)

        def resolve():
            pr.mergeable = False

        pr.update.side_effect = resolve
        self._run(pr)
        self.assertEqual(pr.update.call_count, 1)
        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_FLAG)}\n{REBASE_COMMENT}"
        )
        pr.add_to_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    # no-bot label : skipped regardless of merge state
    def test_skips_no_bot_pr(self):
        """PR with the no-bot label is completely skipped, even if it has conflicts."""
        from ruciobot.checks.base import NO_BOT_LABEL

        pr = self._make_pr(7, mergeable=False, labels=[NO_BOT_LABEL])
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Dependabot author : skipped regardless of merge state
    def test_skips_dependabot_pr(self):
        """PR opened by Dependabot is skipped, even if it has conflicts."""
        pr = self._make_pr(8, mergeable=False)
        pr.user.login = "dependabot[bot]"
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Escalation: warn after NEEDS_REBASE_WARN_DAYS weekdays of inactivity

    def test_warns_inactive_labeled_pr(self):
        """A labeled PR past the warn threshold gets the warning, replacing the flag comment."""
        flag = _bot_comment(KIND_FLAG)
        pr = self._make_pr(
            9, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=PAST_WARN, comments=[flag]
        )
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_WARNING)}\n{REBASE_WARNING_COMMENT}"
        )
        flag.delete.assert_called_once()
        pr.edit.assert_not_called()

    def test_never_deletes_foreign_comments(self):
        """Only the bot's own comments are replaced; quote-replies and humans are untouched."""
        quote_reply = _bot_comment(KIND_WARNING, login="alice")  # marker copied by quote-reply
        human = MagicMock()
        human.body = "ordinary discussion"
        human.user = MagicMock()
        human.user.login = "bob"
        flag = _bot_comment(KIND_FLAG)
        pr = self._make_pr(
            10,
            mergeable=False,
            labels=[NEEDS_REBASE_LABEL],
            updated_at=PAST_WARN,
            comments=[quote_reply, human, flag],
        )
        self._run(pr)
        flag.delete.assert_called_once()
        quote_reply.delete.assert_not_called()
        human.delete.assert_not_called()
        # The quote-reply carries the warning marker but is not the bot's newest
        # own comment, so the PR is warned rather than closed.
        pr.edit.assert_not_called()

    def test_no_escalation_below_threshold(self):
        """A labeled PR inactive for fewer weekdays than the threshold is left alone."""
        pr = self._make_pr(
            11,
            mergeable=False,
            labels=[NEEDS_REBASE_LABEL],
            updated_at=business_days_before(NEEDS_REBASE_WARN_DAYS - 1),
        )
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    # Escalation: close after NEEDS_REBASE_CLOSE_DAYS more weekdays

    def test_closes_warned_pr_with_recap(self):
        """A labeled, already-warned PR past the close threshold is closed with a recap."""
        warning = _bot_comment(KIND_WARNING, created_at=PAST_WARN)
        pr = self._make_pr(
            12,
            mergeable=False,
            labels=[NEEDS_REBASE_LABEL],
            updated_at=PAST_CLOSE,
            comments=[warning],
        )
        self._run(pr)
        expected = f"{bot_marker(KIND_CLOSE)}\n{rebase_close_comment(PAST_WARN)}"
        pr.create_issue_comment.assert_called_once_with(expected)
        self.assertIn(f"A closure warning was issued on {PAST_WARN:%Y-%m-%d}.", expected)
        warning.delete.assert_called_once()
        pr.edit.assert_called_once_with(state="closed")

    def test_comment_scan_failure_warns_instead_of_closing(self):
        """If comments cannot be fetched, the bot re-warns; it never closes unannounced."""
        pr = self._make_pr(13, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=PAST_CLOSE)
        pr.get_issue_comments.side_effect = RuntimeError("boom")
        self._run(pr)
        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_WARNING)}\n{REBASE_WARNING_COMMENT}"
        )
        pr.edit.assert_not_called()

    def test_rate_limit_during_comment_scan_propagates(self):
        """A rate limit raised while scanning comments must reach BaseCheck, not be swallowed."""
        pr = self._make_pr(14, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=PAST_CLOSE)
        pr.get_issue_comments.side_effect = RateLimitExceededException(403, {"message": "hit"}, {})
        with self.assertRaises(RateLimitExceededException):
            self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_rate_limit_during_comment_cleanup_propagates(self):
        """A rate limit raised while cleaning up comments must reach BaseCheck."""
        pr = self._make_pr(15, mergeable=True, labels=[NEEDS_REBASE_LABEL])
        pr.get_issue_comments.side_effect = RateLimitExceededException(403, {"message": "hit"}, {})
        with self.assertRaises(RateLimitExceededException):
            self._run(pr)

    # Failing-tests takes precedence: escalation pauses while its label is present

    def test_failing_tests_label_pauses_escalation(self):
        """A conflicted PR that also has failing tests is left to the failing-tests check."""
        pr = self._make_pr(
            16,
            mergeable=False,
            labels=[NEEDS_REBASE_LABEL, FAILING_TESTS_LABEL],
            updated_at=PAST_CLOSE,
        )
        self._run(pr)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()
        pr.get_issue_comments.assert_not_called()

    def test_failing_tests_label_does_not_block_label_clearing(self):
        """Resolution still clears the needs-rebase label while failing-tests is present."""
        pr = self._make_pr(17, mergeable=True, labels=[NEEDS_REBASE_LABEL, FAILING_TESTS_LABEL])
        self._run(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    def test_weekend_only_gap_takes_no_action(self):
        """A Friday-to-Sunday gap is 0 business days, so no escalation happens."""
        friday = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        sunday = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
        pr = self._make_pr(18, mergeable=False, labels=[NEEDS_REBASE_LABEL], updated_at=friday)
        self._run(pr, now=sunday)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
