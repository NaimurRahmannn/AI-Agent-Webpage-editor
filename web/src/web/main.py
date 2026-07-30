from __future__ import annotations

import sys

from pydantic import ValidationError

from web.session import run_session
from web.settings import Settings


def run() -> None:
    """Start the conversational editing session."""

    try:
        settings = Settings()
    except ValidationError as exc:
        print("Configuration error:", file=sys.stderr)
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc

    run_session(settings)


if __name__ == "__main__":
    run()