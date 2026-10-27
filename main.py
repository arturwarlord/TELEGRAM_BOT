from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from deal_hunter.models import Deal, canonical_url
from deal_hunter.scoring import DealDecision, evaluate_deal
from deal_hunter.sources import fetch_source
from deal_hunter.state import count_published_within_hours, load_state, mark_published, save_state, should_skip_as_duplicate
from deal_hunter.takprodam import NoApprovedSourceError, fetch_configured_takprodam_deals
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


def _coupon_brand_key(deal: Deal) -> str:
    """Approximate merchant identity from coupon title for cross-catalog deduplication."""
    ignored = {
        "promo", "promocode", "промокод", "код", "скидка", "скидки", "скидкой", "скидку",
        "акция", "акции", "бесплатно", "бесплатный", "бесплатная", "до", "от", "на", "в",
        "для", "всем", "новым", "пользователям", "первый", "первого", "повторный", "заказ",
        "товары", "товаров", "октябрь", "октябре", "ноябрь", "ноябре", "декабрь", "декабре",
        "январь", "января", "февраль", "февраля", "март", "марта", "апрель", "апреля",
        "май", "мая", "июнь", "июня", "июль", "июля", "август", "августа", "сентябрь",
        "сентября", "2025", "2026", "2027", "руб", "рублей", "доставка", "интернет-магазин",
        "интернет", "магазин", "получить", "открыть", "актуальные", "новые", "лучшие",
        "все", "весь", "все", "каталог",
    }
    tokens = re.findall(r"[a-zа-яё0-9]+", deal.title.casefold())
    normalized: list[str] = []
    for token in tokens:
        if token in ignored or token.isdigit():
            continue
        # Lightweight Russian case normalization. Keep at least four letters so
        # short brand names and Latin names remain intact.
        for suffix in ("иями", "ями", "ами", "ого", "ему", "ыми", "ими", "ая", "яя", "ое", "ее", "ый", "ий", "ом", "ем", "ых", "их", "ую", "юю", "ой", "ей", "ов", "ев", "ам", "ям", "ах", "ях", "ы", "и", "а", "я", "е", "у", "ю"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                token = token[:-len(suffix)]
                break
        normalized.append(token)
    return " ".join(normalized[:3])


def _direct_page_priority(deal: Deal) -> int:
    path = canonical_url(deal.product_url).casefold()
    if any(marker in path for marker in ("/promo/", "/coupon/", "/offer/", "/deal/", "/deals/", "/action/")):
        return 3
    if any(marker in path for marker in ("/store/", "/shop/", "/shops/")):
        return 2
    return 1


def choose_best_duplicates(deals: list[Deal]) -> list[Deal]:
    """Dedupe exact records, then collapse duplicate coupon cards across source sites."""
    best: dict[str, Deal] = {}
    for deal in deals:
        previous = best.get(deal.key)
        if previous is None:
            best[deal.key] = deal
            continue
        # Prefer the version with a code, more explicit discount, and direct image.
        previous_quality = (
            bool(previous.promo_code),
            previous.discount_percent is not None,
            bool(previous.image_url),
            len(previous.description),
        )
        deal_quality = (
            bool(deal.promo_code),
            deal.discount_percent is not None,
            bool(deal.image_url),
            len(deal.description),
        )
        if deal.price is not None and previous.price is not None and deal.price != previous.price:
            if deal.price < previous.price:
                best[deal.key] = deal
        elif deal_quality > previous_quality:
            best[deal.key] = deal

    unique = list(best.values())
    coupon_groups: dict[tuple[str, int | None], list[Deal]] = {}
    non_coupon: list[Deal] = []
    for deal in unique:
        is_coupon_only = deal.price is None or deal.old_price is None
        brand_key = _coupon_brand_key(deal)
        if not is_coupon_only or not brand_key:
            non_coupon.append(deal)
            continue
        rounded_discount = round(deal.discount_percent) if deal.discount_percent is not None else None
        coupon_groups.setdefault((brand_key, rounded_discount), []).append(deal)

    deduped_coupons: list[Deal] = []
    for group in coupon_groups.values():
        coded: dict[str, Deal] = {}
        uncoded: list[Deal] = []
        for deal in group:
            code = (deal.promo_code or "").strip().casefold()
            if not code:
                uncoded.append(deal)
                continue
            previous = coded.get(code)
            if previous is None or (_direct_page_priority(deal), len(deal.description)) > (
                _direct_page_priority(previous), len(previous.description)
            ):
                coded[code] = deal

        if coded:
            # A coupon card with an explicit code is more actionable than the same
            # merchant/percentage surfaced as a generic store landing card.
            deduped_coupons.extend(coded.values())
        elif uncoded:
            # Same merchant and same explicitly stated discount on several catalogs:
            # prefer the detailed offer page over a generic merchant page.
            deduped_coupons.append(max(uncoded, key=lambda d: (_direct_page_priority(d), len(d.description))))

    return non_coupon + deduped_coupons


def _rank_evaluated_deal(pair: tuple[Deal, DealDecision]) -> float:
    """Prefer actionable deals over broad 'up to' headlines and loyalty-point teasers."""
    deal, decision = pair
    score = decision.score
    has_price_pair = deal.price is not None and deal.old_price is not None

    if has_price_pair:
        score += 35.0
    if deal.promo_code:
        score += 12.0
    if deal.discount_is_maximum:
        score -= 70.0
    if not has_price_pair and not deal.promo_code:
        score -= 12.0

    # A source's store index is less actionable than a specific offer page.
    # Keep the button on the source's public page; don't invent a merchant link.
    canonical = canonical_url(deal.product_url).casefold()
    if any(marker in canonical for marker in ("/store/", "/shop/", "/magazin/")):
        score -= 15.0
    if any(marker in canonical for marker in ("/promo/", "/promocodes/", "/coupon/", "/deals/", "/shares/", "/promokody/")):
        score += 12.0

    title = deal.title.casefold()
    if any(word in title for word in ("балл", "бонус", "кэшбэк", "кешбэк", "вау")) and not has_price_pair:
        score -= 25.0
    if deal.expires_at:
        score += 3.0
    return score


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
    allow_direct_links = env_bool("ALLOW_DIRECT_LINKS", False)
    publishing = publish_enabled and not dry_run
    if allow_direct_links:
        post_notice = os.getenv("DIRECT_POST_NOTICE", "").strip() or (
            "Ссылка ведёт на публичную страницу предложения; условия, цена и наличие могут зависеть от "
            "региона, карты лояльности и требований продавца. Канал не получает комиссию с перехода."
        )
    else:
        post_notice = os.getenv("POST_NOTICE", "").strip() or os.getenv("AD_NOTICE", "").strip()

    print("===== DEAL HUNTER =====")
    print(f"Mode: {'PUBLISH' if publishing else 'DRY-RUN / PREVIEW'}")
    print(f"Link mode: {'PUBLIC OFFER LINKS (NO AFFILIATE)' if allow_direct_links else 'AFFILIATE LINKS REQUIRED FOR PUBLISHING'}")
    partner_api_enabled = bool(os.getenv("TAKPRODAM_API_TOKEN", "").strip())
    print(f"Configured public feed/page sources: {len(sources)}")
    print(f"Takprodam partner API: {'configured' if partner_api_enabled else 'not configured'}")
    print(
        "Safety: public offer pages are used without affiliate tracking; the channel receives no commission."
        if allow_direct_links
        else "Safety: partner link and product image are required before publishing."
    )
    if not sources and not partner_api_enabled:
        print(f"No enabled feeds are configured in {source_config_path.relative_to(ROOT) if source_config_path.is_relative_to(ROOT) else source_config_path}.")
        print("Connect the Takprodam API or add an approved RSS/XML/JSON partner feed. Nothing was published.")
        return 0

    timeout = int(settings.get("fetch_timeout_seconds", 20))
    max_bytes = int(settings.get("max_feed_bytes", 5 * 1024 * 1024))
    max_items = int(settings.get("max_items_per_source", 200))
    all_deals: list[Deal] = []
    source_errors = 0
    successful_sources = 0

    if partner_api_enabled:
        try:
            partner_deals, partner_source_id = fetch_configured_takprodam_deals(timeout=timeout)
            successful_sources += 1
            print(f"[OK] Takprodam API source {partner_source_id}: {len(partner_deals)} promotion product(s)")
            all_deals.extend(partner_deals)
        except NoApprovedSourceError as exc:
            print(f"[WAITING] Takprodam publisher source: {exc}")
        except Exception as exc:
            source_errors += 1
            print(f"[ERROR] Takprodam API: {type(exc).__name__}: {exc}")
    else:
        print("[INFO] Takprodam API is not connected (TAKPRODAM_API_TOKEN secret not set).")

    for source in sources:
        name = str(source.get("name") or "unnamed")
        try:
            items = fetch_source(source, timeout=timeout, max_bytes=max_bytes, max_items=max_items)
            successful_sources += 1
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
    min_coupon_discount = float(settings.get("min_coupon_discount_percent", 10))
    require_image = bool(settings.get("require_image_for_publication", True))

    evaluated: list[tuple[Deal, DealDecision]] = []
    rejection_reasons: Counter[str] = Counter()
    for deal in deals:
        decision = evaluate_deal(
            deal,
            min_discount_percent=min_discount,
            min_savings=min_savings,
            max_discount_percent=max_discount,
            min_coupon_discount_percent=min_coupon_discount,
            require_affiliate=False,
            require_image=False,
        )
        if decision.eligible:
            evaluated.append((deal, decision))
        else:
            rejection_reasons[decision.reason] += 1

    evaluated.sort(key=_rank_evaluated_deal, reverse=True)
    print(f"Deals passing price/expiry filters: {len(evaluated)}")
    if rejection_reasons:
        reasons = ", ".join(f"{reason}: {count}" for reason, count in rejection_reasons.most_common(8))
        print(f"Filtered out: {reasons}")

    if not publishing:
        for index, (deal, decision) in enumerate(evaluated[:20], start=1):
            has_link = "yes" if deal.affiliate_url else "NO"
            has_image = "yes" if deal.image_url else "NO"
            commission_text = f"{deal.commission:g}" if deal.commission is not None else "n/a"
            if deal.price is not None and deal.old_price is not None and decision.discount_percent is not None:
                offer_summary = (
                    f"-{decision.discount_percent:.1f}% | {format_rub(deal.price)} instead of "
                    f"{format_rub(deal.old_price)} | saved {format_rub(decision.savings)}"
                )
            else:
                discount_label = (
                    f"discount {'up to ' if deal.discount_is_maximum else ''}{decision.discount_percent:.1f}%"
                    if decision.discount_percent is not None
                    else "coupon / special offer"
                )
                code_label = f" | code={deal.promo_code}" if deal.promo_code else ""
                offer_summary = f"{discount_label}{code_label}"
            print(
                f"[PREVIEW {index}] {offer_summary} | "
                f"affiliate={has_link} image={has_image} commission={commission_text} | {deal.store} | {deal.title}"
            )
            print(f"  Product URL (not published): {deal.product_url}")
        print("DRY-RUN finished: Telegram was not contacted.")
        if source_errors:
            print(f"[WARN] Source failures: {source_errors}; successfully fetched sources: {successful_sources}.")
        # One unavailable shop must not fail the whole scan if other sources were
        # fetched successfully. Fail only when every configured source errored.
        return 1 if source_errors and successful_sources == 0 else 0

    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        print("[FATAL] TELEGRAM_BOT_TOKEN secret is missing.")
        return 2
    if not os.getenv("TELEGRAM_CHAT_ID", "").strip():
        print("[FATAL] TELEGRAM_CHAT_ID Actions variable is missing.")
        return 2
    if not post_notice:
        print("[FATAL] POST_NOTICE is missing (or AD_NOTICE for affiliate mode).")
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
            min_coupon_discount_percent=min_coupon_discount,
            require_affiliate=not allow_direct_links,
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
            message_id = publisher.publish(
                deal,
                decision,
                post_notice,
                allow_direct_link=allow_direct_links,
            )
            mark_published(deal, state, message_id=message_id)
            save_state(state_path, state)
            posted += 1
            discount_label = f"-{decision.discount_percent:.1f}%" if decision.discount_percent is not None else "coupon"
            print(f"[PUBLISHED] {deal.store} | {discount_label} | message_id={message_id}")
            time.sleep(1.1)
        except Exception as exc:
            publish_errors += 1
            print(f"[PUBLISH ERROR] {deal.store} | {deal.title[:100]} | {type(exc).__name__}: {exc}")

    print(f"Run finished: published={posted}; publish_errors={publish_errors}.")
    if publish_skips:
        print("Publication skips: " + ", ".join(f"{reason}: {count}" for reason, count in publish_skips.most_common()))
    if source_errors:
        print(f"[WARN] Source failures: {source_errors}; successfully fetched sources: {successful_sources}.")
    return 1 if publish_errors or (source_errors and successful_sources == 0) else 0


if __name__ == "__main__":
    sys.exit(main())
