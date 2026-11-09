"""Standalone event worker:  python -m cie.worker [--once]"""
import argparse
import os
import time

from .db import connect, init_db
from .events import process_pending


def main() -> None:
    ap = argparse.ArgumentParser(description="Process outbox events")
    ap.add_argument("--once", action="store_true", help="process what is pending and exit")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    conn = connect(os.environ.get("CIE_DB", "data/cie.db"))
    init_db(conn)
    while True:
        stats = process_pending(conn)
        if any(stats.values()):
            print(stats, flush=True)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
