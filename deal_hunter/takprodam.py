from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import Deal, clean_text, is_http_url, parse_price


API_BASE = "https://api.takprodam.ru/v2/publisher"


class NoApprovedSourceError(RuntimeError):
    """The API token works, but no publisher source is approved yet."""


def _request_json(
    token: str,
    endpoint: str,
    params: dict[str, str | int] | None = None,
    timeout: int = 25,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{API_BASE}/{endpoint.strip('/')}/" + (f"?{query}" if query else "")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "DealHunterBot/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Takprodam API returned HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Takprodam API request failed: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Takprodam API returned an unexpected response type")
    return data


def get_source_list(token: str, timeout: int = 25) -> list[dict[str, Any]]:
    response = _request_json(token, "source", timeout=timeout)
    items = response.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("Takprodam API source-list response has no items array")
    return [item for item in items if isinstance(item, dict)]


def get_approved_sources(token: str, timeout: int = 25) -> list[dict[str, Any]]:
    return [
        source for source in get_source_list(token, timeout=timeout)
        if str(source.get("status", "")).lower() == "approved"
    ]


def resolve_source_id(
    token: str,
    configured_source_id: str | int | None = None,
    timeout: int = 25,
) -> str:
    sources = get_source_list(token, timeout=timeout)
    approved = [
        source for source in sources
        if str(source.get("status", "")).lower() == "approved"
    ]

    if configured_source_id is not None and str(configured_source_id).strip():
        expected = str(configured_source_id).strip()
        match = next((source for source in sources if str(source.get("id")) == expected), None)
        if not match:
            registered = ", ".join(
                f"{source.get('id')}:{clean_text(source.get('title'), 100)}={source.get('status', 'unknown')}"
                for source in sources
            ) or "no sources registered in this account"
            raise NoApprovedSourceError(
                f"TAKPRODAM_SOURCE_ID={expected} was not returned by the API. Registered sources: {registered}"
            )
        if str(match.get("status", "")).lower() != "approved":
            raise NoApprovedSourceError(
                f"Takprodam source {expected} is not approved yet "
                f"(status={match.get('status', 'unknown')}; title={clean_text(match.get('title'), 100)}). "
                "Wait for approval; no products were fetched."
            )
        return expected

    if len(approved) == 1:
        return str(approved[0].get("id"))
    if not approved:
        registered = ", ".join(
            f"{source.get('id')}:{clean_text(source.get('title'), 100)}={source.get('status', 'unknown')}"
            for source in sources
        ) or "no sources registered in this account"
        raise NoApprovedSourceError(
            "No approved Takprodam publisher source found. Add your public Telegram channel as a source "
            "and wait for approval. API-reported sources: " + registered
        )

    choices = ", ".join(
        f"{source.get('id')} ({clean_text(source.get('title') or source.get('source_url'), 120)})"
        for source in approved
    )
    raise RuntimeError(
        "Multiple approved Takprodam sources found; set Actions Variable TAKPRODAM_SOURCE_ID. "
        f"Approved source IDs: {choices}"
    )


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = parse_price(value)
    return result if result is not None and result >= 0 else None


def parse_promotion_items(
    items: list[dict[str, Any]],
    marketplace_filter: str | None = None,
) -> list[Deal]:
    """Map Takprodam/Admitad promotion-product items to the internal deal model."""
    deals: list[Deal] = []
    target_market = (marketplace_filter or "").strip().lower()

    for item in items:
        if not isinstance(item, dict):
            continue
        marketplace = clean_text(item.get("marketplace_title") or item.get("marketplace") or "Маркетплейс", 100)
        if target_market and target_market not in marketplace.lower():
            continue

        title = clean_text(item.get("title"), 300)
        affiliate_url = str(item.get("tracking_link") or "").strip()
        if not title or not is_http_url(affiliate_url):
            continue

        # Promotion-product records include old price and sale price, but may not
        # include the ordinary product URL. Use external_link when the API supplies it;
        # otherwise the tracking link remains the safe dedupe fallback.
        product_url = str(item.get("external_link") or affiliate_url).strip()
        if not is_http_url(product_url):
            product_url = affiliate_url

        old_price = parse_price(item.get("price"))
        price = parse_price(item.get("price_discount"))
        if price is None or old_price is None or old_price <= price:
            continue

        image_url = str(item.get("image_url") or "").strip()
        if not is_http_url(image_url):
            image_url = None

        commission = _float_or_none(item.get("commission"))
        seller = clean_text(item.get("store_title"), 100)
        store = marketplace
        if seller and seller.lower() not in marketplace.lower():
            store = f"{marketplace} · {seller}"[:160]

        deals.append(
            Deal(
                title=title,
                product_url=product_url,
                store=store,
                source=f"takprodam:{marketplace.lower()}",
                price=price,
                old_price=old_price,
                affiliate_url=affiliate_url,
                image_url=image_url,
                currency="RUB",
                description="",
                expires_at=None,
                external_id=str(item.get("product_id") or item.get("id") or "") or None,
                commission=commission,
                legal_text=clean_text(item.get("legal_text"), 500),
            )
        )
    return deals


def fetch_promotion_deals(
    token: str,
    source_id: str | int,
    marketplaces: list[str] | None = None,
    *,
    limit: int = 1000,
    max_pages: int = 1,
    timeout: int = 25,
    subid: str | None = None,
) -> list[Deal]:
    """Fetch approved-partner promotion products and their tracking links."""
    if not token.strip():
        raise ValueError("Takprodam API token is empty")
    if not str(source_id).strip():
        raise ValueError("Takprodam publisher source ID is empty")

    markets = marketplaces or ["Ozon", "Wildberries"]
    page_limit = max(1, min(int(limit), 1000))
    pages = max(1, min(int(max_pages), 10))
    found: list[Deal] = []

    for market in markets:
        normalized = market.strip()
        if not normalized:
            continue
        for page in range(1, pages + 1):
            data = _request_json(
                token,
                "promotion/product",
                params={
                    **{
                        "source_id": str(source_id),
                        "marketplace": normalized,
                        "page": page,
                        "limit": page_limit,
                    },
                    **({"subid": subid.strip()} if subid and subid.strip() else {}),
                },
                timeout=timeout,
            )
            items = data.get("items", [])
            if not isinstance(items, list):
                raise RuntimeError(
                    f"Takprodam API promotion/product response for {normalized} has no items array"
                )
            normalized_items = [item for item in items if isinstance(item, dict)]
            found.extend(parse_promotion_items(normalized_items, normalized))
            if len(normalized_items) < page_limit:
                break
    return found


def fetch_configured_takprodam_deals(timeout: int = 25) -> tuple[list[Deal], str]:
    token = os.getenv("TAKPRODAM_API_TOKEN", "").strip()
    if not token:
        return [], ""

    configured_source_id = os.getenv("TAKPRODAM_SOURCE_ID", "").strip() or None
    source_id = resolve_source_id(token, configured_source_id, timeout=timeout)
    marketplace_setting = os.getenv("TAKPRODAM_MARKETPLACES", "Ozon,Wildberries")
    marketplaces = [name.strip() for name in marketplace_setting.split(",") if name.strip()]
    limit = int(os.getenv("TAKPRODAM_LIMIT", "1000"))
    max_pages = int(os.getenv("TAKPRODAM_MAX_PAGES", "1"))
    # A stable subid lets the commissions API attribute this bot's traffic separately.
    # The value can be overridden in GitHub Actions Variables for campaign experiments.
    subid = os.getenv("TAKPRODAM_SUBID", "").strip() or "telegram_deal_hunter"
    deals = fetch_promotion_deals(
        token,
        source_id,
        marketplaces,
        limit=limit,
        max_pages=max_pages,
        timeout=timeout,
        subid=subid,
    )
    return deals, source_id

def fetch_commissions(
    token: str,
    *,
    subid: str | None = None,
    status: str | None = None,
    limit: int = 1000,
    max_pages: int = 5,
    timeout: int = 25,
) -> list[dict[str, Any]]:
    """Fetch publisher commission records, optionally filtered to this bot's subid."""
    if not token.strip():
        raise ValueError("Takprodam API token is empty")

    page_limit = max(1, min(int(limit), 1000))
    pages = max(1, min(int(max_pages), 10))
    all_items: list[dict[str, Any]] = []

    for page in range(1, pages + 1):
        params: dict[str, str | int] = {"page": page, "limit": page_limit}
        if subid and subid.strip():
            params["subid"] = subid.strip()
        if status and status.strip():
            normalized_status = status.strip().lower()
            if normalized_status not in {"pending", "approved", "declined"}:
                raise ValueError("Commission status must be pending, approved, or declined")
            params["status"] = normalized_status

        response = _request_json(token, "commission", params=params, timeout=timeout)
        items = response.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("Takprodam API commission response has no items array")
        page_items = [item for item in items if isinstance(item, dict)]
        all_items.extend(page_items)

        total_count = response.get("total_count")
        try:
            total_count_value = int(total_count)
        except (TypeError, ValueError):
            total_count_value = 0
        if not page_items or len(page_items) < page_limit:
            break
        if total_count_value > 0 and page * page_limit >= total_count_value:
            break

    return all_items


def summarize_commissions(items: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """Aggregate commission counts and amounts by status without exposing order details."""
    summary: dict[str, dict[str, float | int]] = {}
    for item in items:
        status = str(item.get("status") or "unknown").strip().lower()
        row = summary.setdefault(status, {"count": 0, "commission_amount": 0.0, "cart_amount": 0.0})
        row["count"] = int(row["count"]) + 1
        for field in ("commission_amount", "cart_amount"):
            value = item.get(field)
            try:
                amount = float(value)
            except (TypeError, ValueError):
                continue
            if amount >= 0:
                row[field] = round(float(row[field]) + amount, 2)
    return summary

