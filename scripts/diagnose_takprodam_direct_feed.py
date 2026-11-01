from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deal_hunter.takprodam import fetch_direct_marketplace_promotion_deals  # noqa: E402
from main import _is_specific_product_url  # noqa: E402


def main() -> int:
    token = os.getenv("TAKPRODAM_API_TOKEN", "").strip()
    if not token:
        print("[SKIP] TAKPRODAM_API_TOKEN is not configured. No API request was made.")
        return 0

    source_id = os.getenv("TAKPRODAM_SOURCE_ID", "").strip() or None
    marketplaces = [
        value.strip()
        for value in os.getenv(
            "TAKPRODAM_MARKETPLACES", "Ozon,Wildberries,Yandex Market"
        ).split(",")
        if value.strip()
    ]
    try:
        limit = max(1, min(int(os.getenv("TAKPRODAM_LIMIT", "100")), 1000))
        max_pages = max(1, min(int(os.getenv("TAKPRODAM_MAX_PAGES", "1")), 5))
    except ValueError:
        print("[ERROR] TAKPRODAM_LIMIT and TAKPRODAM_MAX_PAGES must be integers.")
        return 2

    diagnostics: dict[str, int] = {}
    try:
        deals, resolved_source_id = fetch_direct_marketplace_promotion_deals(
            token,
            source_id,
            marketplaces,
            limit=limit,
            max_pages=max_pages,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        # Never print the token or request headers. The API helper does not include them
        # in its exception messages.
        print(f"[ERROR] Direct marketplace feed diagnostic: {type(exc).__name__}: {exc}")
        return 1

    specific = [
        deal for deal in deals
        if _is_specific_product_url(deal.product_url)
        and deal.image_url
        and deal.price is not None
        and deal.old_price is not None
        and deal.old_price > deal.price
    ]

    print("===== TAKPRODAM DIRECT-LINK DIAGNOSTIC (READ-ONLY) =====")
    print("Publishing: DISABLED — this workflow has no Telegram credentials.")
    print("Tracking links: IGNORED")
    print("Commission fields: IGNORED")
    print("Source ID: configured and approved")
    print(f"Marketplaces requested: {', '.join(marketplaces)}")
    for key in sorted(diagnostics):
        print(f"{key}: {diagnostics[key]}")
    print(f"Deals with direct card, photo and verified price pair: {len(specific)}")

    for index, deal in enumerate(specific[:20], start=1):
        discount = deal.discount_percent
        discount_text = f"{discount:.1f}%" if discount is not None else "—"
        print(
            f"[DIRECT ITEM {index}] {deal.store} | {discount_text} | "
            f"{deal.price} RUB vs {deal.old_price} RUB | "
            f"{deal.title[:140]} | {deal.product_url}"
        )

    print(
        "This diagnostic proves only that the API returns usable direct-link product data. "
        "It does not authorize public use of the data or images. Keep publication disabled "
        "until Takprodam confirms direct-link-only use and the applicable ad-marking rules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
