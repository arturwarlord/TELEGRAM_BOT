from __future__ import annotations

import os
import sys
from pathlib import Path

# Match the standalone checker: make the repository root importable when this
# file is executed as "python scripts/report_commissions.py".
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deal_hunter.takprodam import fetch_commissions, summarize_commissions


def main() -> int:
    token = os.getenv("TAKPRODAM_API_TOKEN", "").strip()
    if not token:
        print("[FATAL] TAKPRODAM_API_TOKEN secret is missing.")
        return 2

    subid = os.getenv("TAKPRODAM_SUBID", "").strip() or "telegram_deal_hunter"
    raw_limit = os.getenv("TAKPRODAM_REPORT_LIMIT", "").strip()
    raw_pages = os.getenv("TAKPRODAM_REPORT_MAX_PAGES", "").strip()
    try:
        limit = int(raw_limit or "1000")
        max_pages = int(raw_pages or "5")
    except ValueError:
        print("[FATAL] TAKPRODAM_REPORT_LIMIT and TAKPRODAM_REPORT_MAX_PAGES must be integers.")
        return 2

    print("===== TAKPRODAM COMMISSION REPORT (READ-ONLY) =====")
    print(f"Attribution subid: {subid}")
    print("Safety: reads commission records only; it does not publish or change partner-account data.")

    try:
        items = fetch_commissions(
            token,
            subid=subid,
            limit=limit,
            max_pages=max_pages,
            timeout=25,
        )
    except Exception as exc:
        print(f"[REPORT FAILED] {type(exc).__name__}: {exc}")
        return 1

    print(f"Commission records analysed: {len(items)}")
    if not items:
        print("[INFO] No commission records for this subid yet. This is normal before attributed traffic and orders.")
        return 0

    summary = summarize_commissions(items)
    for status in ("approved", "pending", "declined"):
        row = summary.pop(status, None)
        if row is not None:
            print(
                f"[{status.upper()}] records={row['count']} | "
                f"commission_amount={row['commission_amount']:.2f} | "
                f"cart_amount={row['cart_amount']:.2f}"
            )
    for status, row in sorted(summary.items()):
        print(
            f"[{status.upper()}] records={row['count']} | "
            f"commission_amount={row['commission_amount']:.2f} | "
            f"cart_amount={row['cart_amount']:.2f}"
        )

    if len(items) >= max(1, min(limit, 1000)) * max(1, min(max_pages, 10)):
        print("[INFO] Report reached its configured page limit; increase TAKPRODAM_REPORT_MAX_PAGES for a wider history.")
    print("Report complete. No Telegram post was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
