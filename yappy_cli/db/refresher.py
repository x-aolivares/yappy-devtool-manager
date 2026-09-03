"""Standalone DB token refresher.

Runnable as: python -m yappy_cli.db.refresher <env>

Loops token generation + .env.local write every REFRESH_INTERVAL minutes so the
tunnel keeps a valid token after the CLI process exits (e.g. `db up -r -d`).
"""

from __future__ import annotations

import sys
import time

from ..config import Config
from ..logger import info, success, warn
from .tunnel import REFRESH_INTERVAL, _generate_token, _write_local_env


def main(env: str) -> None:
    cfg = Config.with_env(env)
    info(f"DB token refresher started for '{env}' (refresh every {REFRESH_INTERVAL // 60} min)")
    try:
        while True:
            time.sleep(REFRESH_INTERVAL)
            info("Refreshing DB token...")
            try:
                new_token = _generate_token(cfg)
                _write_local_env(new_token)
                success("Token refreshed (valid for ~15 more min)")
            except Exception as e:
                warn(f"Token refresh failed: {e}")
    except KeyboardInterrupt:
        info("Refresher stopped")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m yappy_cli.db.refresher <env>")
        sys.exit(2)
    main(sys.argv[1])
