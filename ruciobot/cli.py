"""
CLI Entrypoint for RucioBot.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

from .auth import get_github_client
from .checks import CHECKS
from .checks.base import set_bot_login

# Load .env if present
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="RucioBot GitHub Bot")
    parser.add_argument("--action", choices=list(CHECKS.keys()), required=True)
    parser.add_argument("--repo", required=True, help="Repository name (e.g. rucio/rucio)")

    # Arguments when registering as an app on github
    parser.add_argument("--app-id", default=os.getenv("RUCIO_BOT_APP_ID"), help="GitHub App ID")
    parser.add_argument(
        "--private-key",
        default=os.getenv("RUCIO_BOT_PRIVATE_KEY"),
        help="GitHub App Private Key (or path to file)",
    )

    # Arguments when using a personal access token (used for testing)
    parser.add_argument(
        "--token",
        default=os.getenv("GITHUB_TOKEN"),
        help="Personal Access Token or Action Token",
    )

    args = parser.parse_args()

    # Prepare Private Key (handle file path vs content)
    private_key = args.private_key
    if private_key and os.path.isfile(private_key):
        with open(private_key) as f:
            private_key = f.read()

    try:
        gh, bot_login = get_github_client(
            app_id=args.app_id, private_key=private_key, token=args.token, repo_name=args.repo
        )
    except Exception as e:
        print(f"Authentication Error: {e}")
        sys.exit(1)

    # The bot must know its own login to recognise (and only ever delete)
    # its own comments; without it, comment cleanup is disabled.
    set_bot_login(bot_login)

    CHECKS[args.action].run(gh, args.repo)


if __name__ == "__main__":
    main()
