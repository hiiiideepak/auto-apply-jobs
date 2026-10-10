"""Run the dashboard or a single apply cycle.

Examples:
  python -m autoapply serve
  python -m autoapply run --limit 5
  python -m autoapply run --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta


def _logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _start_scheduler() -> None:
    from apscheduler.schedulers.background import BackgroundScheduler

    from autoapply.profile import load_profile
    from autoapply.runner import run_cycle

    hours = int(load_profile().get("run_interval_hours") or 4)
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        lambda: run_cycle(trigger="cron"),
        "interval",
        hours=hours,
        id="apply-cycle",
        max_instances=1,
        coalesce=True,
    )
    if os.environ.get("RUN_ON_STARTUP", "1") not in {"0", "false", "False"}:
        scheduler.add_job(
            lambda: run_cycle(trigger="startup"),
            "date",
            run_date=datetime.now() + timedelta(seconds=15),
            id="apply-startup",
        )
    scheduler.start()


def main() -> None:
    from autoapply.inbox import load_dotenv

    load_dotenv()
    _logging()
    parser = argparse.ArgumentParser(description="Discover and submit job applications")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Start the dashboard, cron, and on-demand endpoint")

    run = sub.add_parser("run", help="Run one cycle and exit")
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    command = args.command or "serve"

    if command == "run":
        from autoapply.runner import run_cycle

        result = run_cycle(trigger="cli", limit=args.limit, dry_run=args.dry_run)
        print(result)
        return

    _start_scheduler()
    from autoapply.web import serve

    serve()


if __name__ == "__main__":
    main()
