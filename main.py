from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from deal_hunter.models import Deal
from deal_hunter.scoring import DealDecision, evaluate_deal
from deal_hunter.sources import fetch_source
from deal_hunter.state import count_published_within_hours, load_state, mark_published, save_state, should_skip_as_duplicate
from deal_hunter.takprodam import fetch_configured_takprodam_deals
from deal_hunter.telegram import TelegramPublisher


ROOT = Path(__file__).resolve().parent


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def format_rub(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def choose_best_duplicates(deals: list[Deal]) -> list[Deal]:
    """Keep one item per canonical product URL, preferring the lowest valid price."""
    best: dict[str, Deal] = {}
    for deal in deals:
        previous = best.get(deal.key)
        if previous is None:
            best[deal.key] = deal
            continue
        if deal.price is not None and (previous.price is None or deal.price < previous.price):
            best[deal.key] = deal
    return list(best.values())


def main() -> int:
    source_config_path = Path(os.getenv("SOURCE_CONFIG_PATH", "config/sources.json"))
    if not source_config_path.is_absolute():
        source_config_path = ROOT / source_config_path
    settings_path = ROOT / "config" / "settings.json"
    state_path = ROOT / "data" / "state.json"

    source_config = read_json(source_config_path, {"sources": []})
    settings = read_json(settings_path, {})
    sources = [
        source for source in source_config.get("sources", [])
        if isinstance(source, dict) and source.get("enabled") is True
    ]

    publish_enabled = env_bool("PUBLISH_ENABLED", False)
    dry_run = env_bool("DRY_RUN", True)
    publishing = publish_enabled and not dry_run

    print("===== DEAL HUNTER =====")
    print(f"Mode: {'PUBLISH' if publishing else 'DRY-RUN / PREVIEW'}")
    partner_api_enabled = bool(os.getenv("TAKPRODAM_API_TOKEN", "").strip())
    print(f"Configured RSS/XML/JSON sources: {len(sources)}")
    print(f"Takprodam partner API: {'configured' if partner_api_enabled else 'not configured'}")
    print("Safety: no post is sent unless the deal has a valid affiliate URL and product image.")
    if not sources and not partner_api_enabled:
        print(f"No enabled feeds are configured in {source_config_path.relative_to(ROOT) if source_config_path.is_relative_to(ROOT) else source_config_path}.")
        print("Connect the Takprodam API or add an approved RSS/XML/JSON partner feed. Nothing was published.")
        return 0

    timeout = int(settings.get("fetch_timeout_seconds", 20))
    max_bytes = int(settings.get("max_feed_bytes", 5 * 1024 * 1024))
    max_items = int(settings.get("max_items_per_source", 200))
    all_deals: list[Deal] = []
    source_errors = 0

    if partner_api_enabled:
        try:
            partner_deals, partner_source_id = fetch_configured_takprodam_deals(timeout=timeout)
            print(f"[OK] Takprodam API source {partner_source_id}: {len(partner_deals)} promotion product(s)")
            all_deals.extend(partner_deals)
        except Exception as exc:
            source_errors += 1
            print(f"[ERROR] Takprodam API: {type(exc).__name__}: {exc}")
    else:
        print("[INFO] Takprodam API is not connected (TAKPRODAM_API_TOKEN secret not set).")

    for source in sources:
        name = str(source.get("name") or "unnamed")
        try:
            items = fetch_source(source, timeout=timeout, max_bytes=max_bytes, max_items=max_items)
            print(f"[OK] {name}: {len(items)} item(s)")
            all_deals.extend(items)
        except Exception as exc:
            source_errors += 1
            print(f"[ERROR] {name}: {type(exc).__name__}: {exc}")

    deals = choose_best_duplicates(all_deals)
    print(f"Fetched {len(all_deals)} item(s); {len(deals)} unique product(s).")

    min_discount = float(settings.get("min_discount_percent", 30))
    min_savings = float(settings.get("min_savings_rub", 100))
    max_discount = float(settings.get("max_discount_percent", 90))
    require_image = bool(settings.get("require_image_for_publication", True))

    evaluated: list[tuple[Deal, DealDecision]] = []
    rejection_reasons: Counter[str] = Counter()
    for deal in deals:
        decision = evaluate_deal(
            deal,
            min_discount_percent=min_discount,
            min_savings=min_savings,
            max_discount_percent=max_discount,
            require_affiliate=False,
            require_image=False,
        )
        if decision.eligible:
            evaluated.append((deal, decision))
        else:
            rejection_reasons[decision.reason] += 1

    evaluated.sort(key=lambda pair: pair[1].score, reverse=True)
    print(f"Deals passing price/expiry filters: {len(evaluated)}")
    if rejection_reasons:
        reasons = ", ".join(f"{reason}: {count}" for reason, count in rejection_reasons.most_common(8))
        print(f"Filtered out: {reasons}")

    if not publishing:
        for index, (deal, decision) in enumerate(evaluated[:20], start=1):
            has_link = "yes" if deal.affiliate_url else "NO"
            has_image = "yes" if deal.image_url else "NO"
            commission_text = f"{deal.commission:g}" if deal.commission is not None else "n/a"
            print(
                f"[PREVIEW {index}] -{decision.discount_percent:.1f}% | "
                f"{format_rub(deal.price)} instead of {format_rub(deal.old_price)} | "
                f"saved {format_rub(decision.savings)} | "
                f"affiliate={has_link} image={has_image} commission={commission_text} | {deal.store} | {deal.title}"
            )
            print(f"  Product URL (not published): {deal.product_url}")
        print("DRY-RUN finished: Telegram was not contacted.")
        return 1 if source_errors and not all_deals else 0

    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        print("[FATAL] TELEGRAM_BOT_TOKEN secret is missing.")
        return 2
    if not os.getenv("TELEGRAM_CHAT_ID", "").strip():
        print("[FATAL] TELEGRAM_CHAT_ID Actions variable is missing.")
        return 2
    ad_notice = os.getenv("AD_NOTICE", "").strip()
    if not ad_notice:
        print("[FATAL] AD_NOTICE Actions variable is missing; configure required partner/ad disclosure before publishing.")
        return 2

    state = load_state(state_path)
    daily_cap = max(1, int(settings.get("max_posts_per_day", 8)))
    published_last_24h = count_published_within_hours(state, hours=24)
    configured_per_run = max(1, int(settings.get("max_posts_per_run", 2)))
    max_posts = min(configured_per_run, max(0, daily_cap - published_last_24h))
    if max_posts <= 0:
        print(f"Daily publication limit reached: {published_last_24h}/{daily_cap} posts in the last 24 hours.")
        return 0

    publisher = TelegramPublisher(
        token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
        timeout=int(settings.get("telegram_timeout_seconds", 25)),
    )
    cooldown = float(settings.get("duplicate_cooldown_hours", 72))
    price_drop = float(settings.get("repost_if_price_drop_percent", 10))
    repost_wait = float(settings.get("repost_min_wait_hours", 6))

    posted = 0
    publish_skips: Counter[str] = Counter()
    publish_errors = 0
    for deal, _preview_decision in evaluated:
        if posted >= max_posts:
            break

        decision = evaluate_deal(
            deal,
            min_discount_percent=min_discount,
            min_savings=min_savings,
            max_discount_percent=max_discount,
            require_affiliate=True,
            require_image=require_image,
        )
        if not decision.eligible:
            publish_skips[decision.reason] += 1
            continue
        if should_skip_as_duplicate(
            deal,
            state,
            cooldown_hours=cooldown,
            repost_if_price_drop_percent=price_drop,
            repost_min_wait_hours=repost_wait,
        ):
            publish_skips["already published / no qualifying price drop"] += 1
            continue

        try:
            message_id = publisher.publish(deal, decision, ad_notice)
            mark_published(deal, state, message_id=message_id)
            save_state(state_path, state)
            posted += 1
            print(f"[PUBLISHED] {deal.store} | -{decision.discount_percent:.1f}% | message_id={message_id}")
            time.sleep(1.1)
        except Exception as exc:
            publish_errors += 1
            print(f"[PUBLISH ERROR] {deal.store} | {deal.title[:100]} | {type(exc).__name__}: {exc}")

    print(f"Run finished: published={posted}; publish_errors={publish_errors}.")
    if publish_skips:
        print("Publication skips: " + ", ".join(f"{reason}: {count}" for reason, count in publish_skips.most_common()))
    return 1 if source_errors or publish_errors else 0


if __name__ == "__main__":
    sys.exit(main())
