"""
Abstract base class for all RucioBot checks.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from github import Github

# PRs carrying this label are completely skipped by all bot checks.
NO_BOT_LABEL = "no-bot"


def is_excluded_from_bot(pr) -> bool:
    """Return True if the PR carries the no-bot exclusion label."""
    return NO_BOT_LABEL in [lbl.name for lbl in pr.labels]


def count_business_days(start: datetime, end: datetime) -> int:
    """Count weekdays (Mon - Fri) between *start* and *end*, exclusive of start.

    Saturdays (weekday 5) and Sundays (weekday 6) are not counted, so
    a PR pushed on Friday afternoon will show zero weekday inactivity
    when the bot runs over the weekend.
    """
    days = 0
    current = start
    # count current, when a full day has already passed before end
    while current + timedelta(days=1) < end:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 0=Mon … 4=Fri
            days += 1
    return days


class BaseCheck(ABC):
    """
    Every check must implement `run`. The CLI calls run(gh, repo_name)
    without needing to know anything about the check's internals.
    """

    @abstractmethod
    def run(self, gh: Github, repo_name: str) -> None:
        """Execute the check against the given repository."""
        ...
