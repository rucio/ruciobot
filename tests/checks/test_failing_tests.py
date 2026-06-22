"""
Tests for failing-tests check.

All `updated_at` and "now" values are pinned to a concrete Monday (2026-03-09)
so that `count_business_days` returns deterministic results regardless of when
the test suite is run.

# Anchor:
#   NOW = Monday 2026-03-09 12:00 UTC
#
# Key business-day offsets from NOW:
#   0 bds = NOW itself (same moment, no business days elapsed)
#   1 bd  = Friday 2026-03-06  (FAILING_TESTS_WARN_DAYS threshold)
#   2 bds = Thursday 2026-03-05
#   4 bds = Tuesday 2026-03-03  (FAILING_TESTS_CLOSE_DAYS=3, so 4 bds ago is past threshold)
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from ruciobot.checks.failing_tests import (
    FAILING_TESTS_CLOSE_DAYS,
    FAILING_TESTS_LABEL,
    FAILING_TESTS_WARN_DAYS,
    process_failing_test_pr,
)

# Pinned "now" — a Monday at noon UTC.
NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def business_days_before(n: int, anchor: datetime = NOW) -> datetime:
    """Return the datetime that is exactly *n* business days before *anchor*."""
    current = anchor
    counted = 0
    while counted < n:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            counted += 1
    return current


# Pre-computed dates used across tests
SAME_MOMENT = NOW  # 0 bds elapsed — very recent
WARN_DATE = business_days_before(FAILING_TESTS_WARN_DAYS + 1)  # > warn threshold
CLOSE_DATE = business_days_before(FAILING_TESTS_CLOSE_DAYS + 1)  # > close threshold
ACTIVE_DATE = (
    business_days_before(FAILING_TESTS_WARN_DAYS - 1) if FAILING_TESTS_WARN_DAYS > 1 else NOW
)  # < warn threshold (use NOW when WARN=1)


class TestFailingTestPRs(unittest.TestCase):
    def _mock_now(self, mock_dt):
        mock_dt.now.return_value = NOW

    def create_mock_pr(self, number, updated_at, labels=[]):
        pr = MagicMock()
        pr.number = number
        pr.title = f"PR {number}"
        pr.updated_at = updated_at
        label_mocks = []
        for lbl in labels:
            m = MagicMock()
            m.name = lbl
            label_mocks.append(m)
        pr.labels = label_mocks
        return pr

    def create_mock_repo(self, check_conclusions):
        mock_repo = MagicMock()
        mock_runs = []
        for conclusion in check_conclusions:
            run = MagicMock()
            run.conclusion = conclusion
            mock_runs.append(run)
        mock_repo.get_commit.return_value.get_check_runs.return_value = mock_runs
        return mock_repo

    # Core behaviour tests

    def test_warns_pr_with_failing_tests_after_1_day(self):
        """PR with failing checks inactive >= 1 business day should be warned."""
        pr = self.create_mock_pr(1, updated_at=WARN_DATE, labels=[])
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_called_with(FAILING_TESTS_LABEL)
        pr.create_issue_comment.assert_called_once()
        pr.edit.assert_not_called()

    def test_closes_pr_with_failing_tests_after_3_days(self):
        """PR already labeled 'failing-tests' and inactive >= 3 business days should be closed."""
        pr = self.create_mock_pr(1, updated_at=CLOSE_DATE, labels=[FAILING_TESTS_LABEL])
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.edit.assert_called_with(state="closed")
        pr.create_issue_comment.assert_called_once()

    def test_removes_label_when_tests_pass(self):
        """PR labeled 'failing-tests' whose CI is now green should have the label removed."""
        pr = self.create_mock_pr(1, updated_at=ACTIVE_DATE, labels=[FAILING_TESTS_LABEL])
        repo = self.create_mock_repo(["success"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.remove_from_labels.assert_called_once_with(FAILING_TESTS_LABEL)
        pr.edit.assert_not_called()
        pr.create_issue_comment.assert_not_called()

    def test_ignores_recently_active_failing_pr(self):
        """PR with failing checks but 0 business days elapsed should not be warned or closed."""
        pr = self.create_mock_pr(1, updated_at=SAME_MOMENT, labels=[])
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_ignores_pr_with_passing_tests(self):
        """PR with only passing checks should not be warned."""
        pr = self.create_mock_pr(1, updated_at=WARN_DATE, labels=[])
        repo = self.create_mock_repo(["success", "success"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_ignores_pr_with_no_checks(self):
        """PR with no check runs at all should not be warned."""
        pr = self.create_mock_pr(1, updated_at=WARN_DATE, labels=[])
        repo = self.create_mock_repo([])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_does_not_close_warned_pr_if_recently_active(self):
        """PR labeled 'failing-tests' but recently active should NOT be closed."""
        # Only 1 business day ago = warn threshold, but not yet at CLOSE_DAYS (3)
        pr = self.create_mock_pr(
            1,
            updated_at=business_days_before(FAILING_TESTS_WARN_DAYS),
            labels=[FAILING_TESTS_LABEL],
        )
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.edit.assert_not_called()

    def test_skips_pr_with_no_bot_label(self):
        """PR with 'no-bot' label is completely skipped by all failing-tests checks."""
        from ruciobot.checks.base import NO_BOT_LABEL

        pr = self.create_mock_pr(1, updated_at=WARN_DATE, labels=[NO_BOT_LABEL])
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_skips_dependabot_pr(self):
        """PR opened by Dependabot is skipped, even with failing tests and inactivity."""
        pr = self.create_mock_pr(1, updated_at=WARN_DATE, labels=[])
        pr.user.login = "dependabot[bot]"
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_skips_draft_pr(self):
        """A draft PR is skipped, even with failing tests and inactivity."""
        pr = self.create_mock_pr(1, updated_at=WARN_DATE, labels=[])
        pr.draft = True
        repo = self.create_mock_repo(["failure"])
        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            self._mock_now(mock_dt)
            process_failing_test_pr(pr, repo)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    # Weekend-aware tests

    def test_does_not_warn_when_only_weekend_has_passed(self):
        """PR pushed on a Friday with failing tests; bot runs Sunday — 0 business days, no warn.

        Timeline:
          - updated_at = Friday 2026-03-06 17:00 UTC
          - "now"      = Sunday 2026-03-08 09:00 UTC
          Business days between Fri 17:00 and Sun 09:00 = 0
          (FAILING_TESTS_WARN_DAYS = 1, so 0 < 1 → no action).
        """
        friday = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        sunday = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)

        pr = self.create_mock_pr(1, updated_at=friday, labels=[])
        repo = self.create_mock_repo(["failure"])

        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            mock_dt.now.return_value = sunday
            process_failing_test_pr(pr, repo)

        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_warns_after_business_days_span_weekend(self):
        """PR pushed on Friday with failing tests; bot runs following Tuesday → 2 bds → warn.

        Timeline:
          - updated_at = Friday  2026-03-06 17:00 UTC
          - "now"      = Tuesday 2026-03-10 09:00 UTC
          Business days: Mon + Tue = 2 >= FAILING_TESTS_WARN_DAYS (1) → warn.
        """
        friday = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        tuesday = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)

        pr = self.create_mock_pr(1, updated_at=friday, labels=[])
        repo = self.create_mock_repo(["failure"])

        with patch("ruciobot.checks.failing_tests.datetime") as mock_dt:
            mock_dt.now.return_value = tuesday
            process_failing_test_pr(pr, repo)

        pr.add_to_labels.assert_called_with(FAILING_TESTS_LABEL)
        pr.create_issue_comment.assert_called_once()
        pr.edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
