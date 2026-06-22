import unittest
from unittest.mock import MagicMock

from ruciobot.checks.needs_rebase import (
    NEEDS_REBASE_LABEL,
    REBASE_COMMENT,
    process_needs_rebase_pr,
)


class TestNeedsRebaseCheck(unittest.TestCase):
    def _make_pr(self, number: int, mergeable, labels: list[str] | None = None):
        pr = MagicMock()
        pr.number = number
        pr.mergeable = mergeable

        label_mocks = []
        for lbl in labels or []:
            m = MagicMock()
            m.name = lbl
            label_mocks.append(m)
        pr.labels = label_mocks

        return pr

    # Happy-path: PR has conflicts, no label yet : comment + label
    def test_flags_conflicting_pr(self):
        """PR with merge conflicts (mergeable=False) should be commented on and labeled."""
        pr = self._make_pr(1, mergeable=False)
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_called_once_with(REBASE_COMMENT)
        pr.add_to_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    # Already labeled : no duplicate comment / label
    def test_skips_already_labeled_pr(self):
        """A PR already carrying the needs-rebase label should not be re-commented on."""
        pr = self._make_pr(2, mergeable=False, labels=[NEEDS_REBASE_LABEL])
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Conflicts resolved : label removed
    def test_removes_label_when_conflicts_resolved(self):
        """A PR that was labeled needs-rebase but is now mergeable should have the label removed."""
        pr = self._make_pr(3, mergeable=True, labels=[NEEDS_REBASE_LABEL])
        process_needs_rebase_pr(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REBASE_LABEL)

    # Cleanly mergeable, no label : no-op
    def test_ignores_clean_pr(self):
        """A mergeable PR with no needs-rebase label requires no action."""
        pr = self._make_pr(4, mergeable=True)
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.remove_from_labels.assert_not_called()

    # GitHub hasn't determined mergeability yet : skip
    def test_skips_when_mergeability_unknown(self):
        """PR whose mergeability is None (not yet computed) should be skipped entirely."""
        pr = self._make_pr(5, mergeable=None)
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # no-bot label : skipped regardless of merge state
    def test_skips_no_bot_pr(self):
        """PR with the no-bot label is completely skipped, even if it has conflicts."""
        from ruciobot.checks.base import NO_BOT_LABEL

        pr = self._make_pr(6, mergeable=False, labels=[NO_BOT_LABEL])
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Dependabot author : skipped regardless of merge state
    def test_skips_dependabot_pr(self):
        """PR opened by Dependabot is skipped, even if it has conflicts."""
        pr = self._make_pr(7, mergeable=False)
        pr.user.login = "dependabot[bot]"
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()

    # Draft PR : skipped regardless of merge state
    def test_skips_draft_pr(self):
        """A draft PR is skipped, even if it has conflicts."""
        pr = self._make_pr(8, mergeable=False)
        pr.draft = True
        process_needs_rebase_pr(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()


if __name__ == "__main__":
    unittest.main()
