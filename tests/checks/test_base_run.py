"""
Tests for the shared ``BaseCheck.run`` loop.

These cover the cross-cutting behaviour that lives in the base class rather than
in any individual check:

  * the open PRs are snapshotted (pagination fully consumed) *before* any PR is
    processed, so mutating a PR mid-run cannot reorder later pages and skip PRs;
  * a failure on one PR does not abort the rest of the sweep;
  * a secondary rate limit is retried after backing off, and a primary rate
    limit stops the run.
"""

import unittest
from unittest.mock import MagicMock, patch

from github.GithubException import RateLimitExceededException

from ruciobot.checks.base import MAX_SECONDARY_RATE_LIMIT_RETRIES, BaseCheck


def _make_pr(number):
    pr = MagicMock()
    pr.number = number
    return pr


class RecordingPulls:
    """Mimics a PaginatedList: yields PRs lazily and records when each is pulled.

    Used to prove that ``run`` exhausts pagination before processing any PR.
    """

    def __init__(self, prs, log):
        self._prs = prs
        self._log = log

    def __iter__(self):
        for pr in self._prs:
            self._log.append(("fetched", pr.number))
            yield pr


class DummyCheck(BaseCheck):
    """A check whose per-PR behaviour is scripted for testing."""

    summary = "dummy check"

    def __init__(self, log, behaviour=None):
        self.log = log
        self.behaviour = behaviour or {}

    def process(self, pr, repo):
        self.log.append(("processed", pr.number))
        action = self.behaviour.get(pr.number)
        if action is not None:
            action()


def _make_gh(prs, log):
    repo = MagicMock()
    repo.get_pulls.return_value = RecordingPulls(prs, log)
    gh = MagicMock()
    gh.get_repo.return_value = repo
    return gh, repo


def _secondary_exc(retry_after="1"):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return RateLimitExceededException(
        403,
        {"message": "You have exceeded a secondary rate limit."},
        headers,
        None,
    )


def _primary_exc():
    return RateLimitExceededException(
        403, {"message": "API rate limit exceeded for user 123."}, {}, None
    )


class TestBaseRun(unittest.TestCase):
    def test_fetches_all_open_prs_before_processing_any(self):
        """run() must snapshot every open PR before mutating, so none is skipped."""
        log = []
        prs = [_make_pr(1), _make_pr(2), _make_pr(3)]
        gh, repo = _make_gh(prs, log)

        DummyCheck(log).run(gh, "rucio/rucio")

        repo.get_pulls.assert_called_once_with(state="open")
        # Every "fetched" entry must precede the first "processed" entry.
        kinds = [kind for kind, _ in log]
        first_processed = kinds.index("processed")
        self.assertNotIn("fetched", kinds[first_processed:])
        # And all three PRs were processed.
        processed = [n for kind, n in log if kind == "processed"]
        self.assertEqual(processed, [1, 2, 3])

    def test_one_pr_failure_does_not_abort_the_sweep(self):
        """A generic error on one PR must not stop the others from processing."""
        log = []
        prs = [_make_pr(1), _make_pr(2), _make_pr(3)]
        gh, _ = _make_gh(prs, log)

        def boom():
            raise ValueError("kaboom")

        DummyCheck(log, behaviour={2: boom}).run(gh, "rucio/rucio")

        processed = [n for kind, n in log if kind == "processed"]
        self.assertEqual(processed, [1, 2, 3])

    @patch("ruciobot.checks.base.time.sleep")
    def test_retries_then_succeeds_on_secondary_rate_limit(self, mock_sleep):
        """A secondary rate limit is retried after backing off, then succeeds."""
        log = []
        prs = [_make_pr(1), _make_pr(2)]
        gh, _ = _make_gh(prs, log)

        state = {"raised": False}

        def flaky():
            if not state["raised"]:
                state["raised"] = True
                raise _secondary_exc(retry_after="1")

        DummyCheck(log, behaviour={1: flaky}).run(gh, "rucio/rucio")

        mock_sleep.assert_called_once_with(1)
        processed = [n for kind, n in log if kind == "processed"]
        # PR 1 processed twice (initial failure + successful retry), PR 2 once.
        self.assertEqual(processed, [1, 1, 2])

    @patch("ruciobot.checks.base.time.sleep")
    def test_persistent_secondary_limit_skips_pr_and_continues(self, mock_sleep):
        """If a PR keeps hitting the secondary limit, skip it and move on."""
        log = []
        prs = [_make_pr(1), _make_pr(2)]
        gh, _ = _make_gh(prs, log)

        def always():
            raise _secondary_exc(retry_after="1")

        DummyCheck(log, behaviour={1: always}).run(gh, "rucio/rucio")

        self.assertEqual(mock_sleep.call_count, MAX_SECONDARY_RATE_LIMIT_RETRIES)
        # PR 2 is still processed despite PR 1 exhausting its retries.
        self.assertIn(2, [n for kind, n in log if kind == "processed"])

    @patch("ruciobot.checks.base.time.sleep")
    def test_primary_rate_limit_stops_the_run(self, mock_sleep):
        """A primary rate limit halts the sweep, since nothing else can succeed."""
        log = []
        prs = [_make_pr(1), _make_pr(2), _make_pr(3)]
        gh, _ = _make_gh(prs, log)

        def primary():
            raise _primary_exc()

        DummyCheck(log, behaviour={2: primary}).run(gh, "rucio/rucio")

        mock_sleep.assert_not_called()
        processed = [n for kind, n in log if kind == "processed"]
        # PR 1 and PR 2 attempted; PR 3 never reached.
        self.assertEqual(processed, [1, 2])


if __name__ == "__main__":
    unittest.main()
