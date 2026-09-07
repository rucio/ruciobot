"""Tests for the PR-template marker check."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ruciobot.checks.base import NO_BOT_LABEL, bot_marker, set_bot_login
from ruciobot.checks.pr_template import (
    CLOSE_COMMENT,
    KIND_CLOSE,
    KIND_WARNING,
    MISSING_TEMPLATE_LABEL,
    PR_TEMPLATE_MARKER,
    TEMPLATE_ENFORCEMENT_DATE,
    WARNING_COMMENT,
    process_pr_template,
)

BOT_LOGIN = "ruciobot[bot]"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
RECENT_WARNING = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
OLD_WARNING = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)


def _bot_comment(kind: str, created_at: datetime, login: str = BOT_LOGIN):
    comment = MagicMock()
    comment.body = f"{bot_marker(kind)}\nsome text"
    comment.user = MagicMock()
    comment.user.login = login
    comment.created_at = created_at
    return comment


def _make_pr(
    *,
    body: str | None = "",
    created_at: datetime = TEMPLATE_ENFORCEMENT_DATE,
    draft: bool = False,
    labels: list[str] | None = None,
    comments: list | None = None,
    author: str = "alice",
):
    pr = MagicMock()
    pr.number = 1
    pr.body = body
    pr.created_at = created_at
    pr.draft = draft
    pr.labels = [SimpleNamespace(name=label) for label in (labels or [])]
    pr.user = MagicMock()
    pr.user.login = author
    pr.get_issue_comments.return_value = list(comments or [])
    return pr


def _run(pr, now: datetime = NOW) -> None:
    with patch("ruciobot.checks.pr_template.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        process_pr_template(pr)


class TestPRTemplateCheck(unittest.TestCase):
    def setUp(self):
        set_bot_login(BOT_LOGIN)

    def tearDown(self):
        set_bot_login(None)

    def test_grandfathers_pr_created_before_enforcement(self):
        pr = _make_pr(created_at=datetime(2026, 9, 7, 23, 59, 59, tzinfo=UTC))

        _run(pr)

        pr.get_issue_comments.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_enforces_marker_at_boundary(self):
        pr = _make_pr(created_at=TEMPLATE_ENFORCEMENT_DATE)

        _run(pr)

        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_WARNING)}\n{WARNING_COMMENT}"
        )
        pr.add_to_labels.assert_called_once_with(MISSING_TEMPLATE_LABEL)
        pr.edit.assert_not_called()

    def test_accepts_marker_without_validating_other_content(self):
        pr = _make_pr(body=PR_TEMPLATE_MARKER)

        _run(pr)

        pr.get_issue_comments.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_skips_draft_pr(self):
        pr = _make_pr(draft=True)

        _run(pr)

        pr.get_issue_comments.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_recent_warning_is_not_duplicated(self):
        warning = _bot_comment(KIND_WARNING, RECENT_WARNING)
        pr = _make_pr(labels=[MISSING_TEMPLATE_LABEL], comments=[warning])

        _run(pr)

        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.edit.assert_not_called()

    def test_closes_one_weekday_after_warning(self):
        warning = _bot_comment(KIND_WARNING, OLD_WARNING)
        pr = _make_pr(labels=[MISSING_TEMPLATE_LABEL], comments=[warning])

        _run(pr)

        warning.delete.assert_called_once()
        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_CLOSE)}\n{CLOSE_COMMENT}"
        )
        pr.edit.assert_called_once_with(state="closed")

    def test_weekend_does_not_count_towards_deadline(self):
        friday = datetime(2026, 9, 11, 17, 0, tzinfo=UTC)
        sunday = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
        warning = _bot_comment(KIND_WARNING, friday)
        pr = _make_pr(labels=[MISSING_TEMPLATE_LABEL], comments=[warning])

        _run(pr, now=sunday)

        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_manual_label_removal_does_not_restart_deadline(self):
        warning = _bot_comment(KIND_WARNING, OLD_WARNING)
        pr = _make_pr(comments=[warning])

        _run(pr)

        pr.add_to_labels.assert_called_once_with(MISSING_TEMPLATE_LABEL)
        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_CLOSE)}\n{CLOSE_COMMENT}"
        )
        pr.edit.assert_called_once_with(state="closed")

    def test_missing_warning_is_reissued_before_closure(self):
        pr = _make_pr(labels=[MISSING_TEMPLATE_LABEL])

        _run(pr)

        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_WARNING)}\n{WARNING_COMMENT}"
        )
        pr.edit.assert_not_called()

    def test_marker_removes_label_and_warning(self):
        warning = _bot_comment(KIND_WARNING, OLD_WARNING)
        foreign = _bot_comment(KIND_WARNING, OLD_WARNING, login="alice")
        pr = _make_pr(
            body=f"Description\n\n{PR_TEMPLATE_MARKER}",
            labels=[MISSING_TEMPLATE_LABEL],
            comments=[warning, foreign],
        )

        _run(pr)

        pr.remove_from_labels.assert_called_once_with(MISSING_TEMPLATE_LABEL)
        warning.delete.assert_called_once()
        foreign.delete.assert_not_called()
        pr.edit.assert_not_called()

    def test_reopened_unfixed_pr_is_closed_again(self):
        closing_comment = _bot_comment(KIND_CLOSE, OLD_WARNING)
        pr = _make_pr(labels=[MISSING_TEMPLATE_LABEL], comments=[closing_comment])

        _run(pr)

        pr.create_issue_comment.assert_called_once_with(
            f"{bot_marker(KIND_CLOSE)}\n{CLOSE_COMMENT}"
        )
        pr.edit.assert_called_once_with(state="closed")

    def test_skips_no_bot_pr_and_clears_existing_state(self):
        warning = _bot_comment(KIND_WARNING, RECENT_WARNING)
        pr = _make_pr(labels=[NO_BOT_LABEL, MISSING_TEMPLATE_LABEL], comments=[warning])

        _run(pr)

        pr.remove_from_labels.assert_called_once_with(MISSING_TEMPLATE_LABEL)
        warning.delete.assert_called_once()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_skips_dependabot_pr(self):
        pr = _make_pr(author="dependabot[bot]")

        _run(pr)

        pr.get_issue_comments.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
