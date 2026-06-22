"""
Tests for the shared exclusion helpers in ``base`` that decide whether the bot
should touch a PR at all: PRs opened by Dependabot, draft PRs, and PRs carrying
the no-bot label are skipped by every check.
"""

import unittest
from unittest.mock import MagicMock

from ruciobot.checks.base import (
    NO_BOT_LABEL,
    exclusion_reason,
    is_dependabot_pr,
    is_draft_pr,
)


def _make_pr(login=None, labels=(), draft=False):
    pr = MagicMock()
    pr.user = None if login is None else MagicMock(login=login)
    pr.draft = draft
    label_mocks = []
    for lbl in labels:
        m = MagicMock()
        m.name = lbl
        label_mocks.append(m)
    pr.labels = label_mocks
    return pr


class TestIsDependabotPR(unittest.TestCase):
    def test_recognises_dependabot(self):
        self.assertTrue(is_dependabot_pr(_make_pr(login="dependabot[bot]")))

    def test_recognises_legacy_dependabot_preview(self):
        self.assertTrue(is_dependabot_pr(_make_pr(login="dependabot-preview[bot]")))

    def test_regular_author_is_not_dependabot(self):
        self.assertFalse(is_dependabot_pr(_make_pr(login="octocat")))

    def test_lookalike_author_is_not_dependabot(self):
        """Only the exact bot logins count, not a human who mimics the name."""
        self.assertFalse(is_dependabot_pr(_make_pr(login="dependabot")))

    def test_missing_user_is_not_dependabot(self):
        """A PR from a deleted account (user is None) must not blow up."""
        self.assertFalse(is_dependabot_pr(_make_pr(login=None)))


class TestIsDraftPR(unittest.TestCase):
    def test_draft_pr_is_recognised(self):
        self.assertTrue(is_draft_pr(_make_pr(draft=True)))

    def test_ready_pr_is_not_a_draft(self):
        self.assertFalse(is_draft_pr(_make_pr(draft=False)))

    def test_missing_draft_attribute_is_not_a_draft(self):
        """A non-bool/absent ``draft`` must be treated as not-draft, never truthy."""
        self.assertFalse(is_draft_pr(object()))


class TestExclusionReason(unittest.TestCase):
    def test_dependabot_pr_is_excluded(self):
        self.assertEqual(
            exclusion_reason(_make_pr(login="dependabot[bot]")),
            "was opened by Dependabot",
        )

    def test_draft_pr_is_excluded(self):
        self.assertEqual(exclusion_reason(_make_pr(login="octocat", draft=True)), "is a draft")

    def test_no_bot_label_pr_is_excluded(self):
        self.assertEqual(
            exclusion_reason(_make_pr(login="octocat", labels=[NO_BOT_LABEL])),
            f"has '{NO_BOT_LABEL}' label",
        )

    def test_dependabot_takes_precedence_over_label(self):
        reason = exclusion_reason(_make_pr(login="dependabot[bot]", labels=[NO_BOT_LABEL]))
        self.assertEqual(reason, "was opened by Dependabot")

    def test_draft_takes_precedence_over_label(self):
        reason = exclusion_reason(_make_pr(login="octocat", labels=[NO_BOT_LABEL], draft=True))
        self.assertEqual(reason, "is a draft")

    def test_regular_pr_is_not_excluded(self):
        self.assertIsNone(exclusion_reason(_make_pr(login="octocat", labels=["enhancement"])))


if __name__ == "__main__":
    unittest.main()
