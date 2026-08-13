"""Sync worker entrypoint.

Split from the API so a long sync cannot take the request path down and the
worker can restart on its own.

Single process by design: SQLite has one writer, so claiming is a conditional
UPDATE guarded by a partial unique index. Postgres is the first swap if a
second worker is ever needed; claiming already has the right shape.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

from core import db
from core.sync import poll_once

POLL_INTERVAL_SEC = float(os.getenv("WORKER_POLL_INTERVAL_SEC", "2"))

log = logging.getLogger("worker")

_stopping = False


def _handle_signal(signum, _frame):
    """Stop after the current run. Interrupting mid-file would leave the
    heartbeat to go stale and be reclaimed - correct, but noisy."""
    global _stopping
    log.info("signal %s received; finishing the current run then stopping", signum)
    _stopping = True


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    db.init()
    log.info("worker started; polling every %.1fs", POLL_INTERVAL_SEC)

    while not _stopping:
        try:
            if poll_once() is None:
                # Nothing queued; sleep rather than spin.
                time.sleep(POLL_INTERVAL_SEC)
        except Exception:
            # The loop outlives any single run. run_sync already marked a hard
            # failure in the database; anything here is a bug, not a reason to
            # stop serving every other tenant.
            log.exception("unexpected error in worker loop; continuing")
            time.sleep(POLL_INTERVAL_SEC)

    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
