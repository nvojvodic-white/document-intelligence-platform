"""Sync worker entrypoint.

The second of the two processes that share this codebase. The split exists for
two reasons and no others: a long sync must not be able to take the request
path down with it, and the worker must be restartable on its own. Do not split
further without a comparable reason.

Single process by design. SQLite has one writer, so run claiming is a single
conditional UPDATE guarded by a partial unique index rather than a queue with
leases. Postgres is the first thing to swap in if a second worker is ever
needed - at which point claiming already has the right shape.
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
    """Stop after the current run rather than mid-file.

    A run interrupted here would leave its heartbeat to go stale and be
    reclaimed as failed, which is correct but noisy. Finishing the file in hand
    keeps counters honest.
    """
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
                # Nothing queued. Sleep rather than spin.
                time.sleep(POLL_INTERVAL_SEC)
        except Exception:
            # The loop must outlive any single run. A run that fails hard has
            # already been marked failed in the database by run_sync; anything
            # reaching here is a bug worth logging with a stack trace, not a
            # reason to stop serving every other tenant.
            log.exception("unexpected error in worker loop; continuing")
            time.sleep(POLL_INTERVAL_SEC)

    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
