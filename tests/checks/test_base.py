"""
Tests for the shared helpers in ``base``: the exclusion rules that decide
whether the bot should touch a PR at all, and the single-bot-comment
machinery (marker parsing, own-comment detection, delete-and-repost).
"""

import unittest
from unittest.mock import MagicMock

from ruciobot.checks.base import (
    NO_BOT_LABEL,
    bot_comment_kind,
    bot_marker,
    delete_bot_comments,
    exclusion_reason,
    is_dependabot_pr,
    is_own_comment,
    post_bot_comment,
    set_bot_login,
)

BOT_LOGIN = "ruciobot[bot]"


def _comment(body, login=BOT_LOGIN):
    comment = MagicMock()
    comment.body = body
    comment.user = MagicMock()
    comment.user.login = login
    return comment


def _bot_comment(kind, login=BOT_LOGIN):
    return _comment(f"{bot_marker(kind)}\nsome text", login=login)


def _make_pr(login=None, labels=()):
    pr = MagicMock()
    pr.user = None if login is None else MagicMock(login=login)
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


class TestExclusionReason(unittest.TestCase):
    def test_dependabot_pr_is_excluded(self):
        self.assertEqual(
            exclusion_reason(_make_pr(login="dependabot[bot]")),
            "was opened by Dependabot",
        )

    def test_no_bot_label_pr_is_excluded(self):
        self.assertEqual(
            exclusion_reason(_make_pr(login="octocat", labels=[NO_BOT_LABEL])),
            f"has '{NO_BOT_LABEL}' label",
        )

    def test_dependabot_takes_precedence_over_label(self):
        reason = exclusion_reason(_make_pr(login="dependabot[bot]", labels=[NO_BOT_LABEL]))
        self.assertEqual(reason, "was opened by Dependabot")

    def test_regular_pr_is_not_excluded(self):
        self.assertIsNone(exclusion_reason(_make_pr(login="octocat", labels=["enhancement"])))


class TestBotCommentHelpers(unittest.TestCase):
    def setUp(self):
        set_bot_login(BOT_LOGIN)

    def tearDown(self):
        set_bot_login(None)

    def _make_pr(self, comments):
        pr = MagicMock()
        pr.number = 1
        pr.get_issue_comments.return_value = list(comments)
        return pr

    # Marker parsing

    def test_kind_roundtrip(self):
        self.assertEqual(bot_comment_kind(f"{bot_marker('stale-warning')}\nbody"), "stale-warning")

    def test_no_marker_has_no_kind(self):
        self.assertIsNone(bot_comment_kind("just a regular comment"))
        self.assertIsNone(bot_comment_kind(None))
        self.assertIsNone(bot_comment_kind("<!-- ruciobot:unterminated"))

    # Own-comment detection

    def test_own_comment_requires_marker_and_login(self):
        self.assertTrue(is_own_comment(_bot_comment("stale-warning")))
        self.assertFalse(is_own_comment(_bot_comment("stale-warning", login="alice")))
        self.assertFalse(is_own_comment(_comment("no marker here")))

    def test_nothing_is_own_without_known_login(self):
        """Until the bot knows its own login, no comment may be treated as its own."""
        set_bot_login(None)
        self.assertFalse(is_own_comment(_bot_comment("stale-warning")))

    # Delete-and-repost

    def test_post_replaces_previous_bot_comment(self):
        old = _bot_comment("stale-warning")
        pr = self._make_pr([old])
        post_bot_comment(pr, "stale-close", "closing now")
        old.delete.assert_called_once()
        pr.create_issue_comment.assert_called_once_with(f"{bot_marker('stale-close')}\nclosing now")

    def test_post_never_touches_foreign_comments(self):
        """A quote-reply copies the marker verbatim, but its author is not the bot."""
        quote_reply = _bot_comment("stale-warning", login="alice")
        plain = _comment("ordinary discussion", login="bob")
        pr = self._make_pr([quote_reply, plain])
        post_bot_comment(pr, "stale-close", "closing now")
        quote_reply.delete.assert_not_called()
        plain.delete.assert_not_called()

    def test_delete_respects_kind_prefix(self):
        ours = _bot_comment("needs-rebase-flag")
        other_check = _bot_comment("failing-tests-warning")
        pr = self._make_pr([ours, other_check])
        delete_bot_comments(pr, "needs-rebase-")
        ours.delete.assert_called_once()
        other_check.delete.assert_not_called()

    def test_delete_is_disabled_without_known_login(self):
        set_bot_login(None)
        comment = _bot_comment("stale-warning")
        pr = self._make_pr([comment])
        delete_bot_comments(pr)
        pr.get_issue_comments.assert_not_called()
        comment.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
