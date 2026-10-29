from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import Deal, clean_text, is_http_url, parse_price


API_BASE = "https://api.takprodam.ru/v2/publisher"


def _env_int(name: str, default: int) -> int:
    """Read an optional integer environment variable; blank or malformed values use the default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

_DIRECT_MARKETPLACE_HOSTS = {
    "ozon": ("ozon.ru", "ozon.kz", "ozon.by", "ozon.uz"),
    "wildberries": ("wildberries.ru", "wildberries.by", "wildberries.kz", "wildberries.uz", "wb.ru"),
    "avito": ("avito.ru",),
    "aliexpress": ("aliexpress.ru", "aliexpress.com", "aliexpress.kz", "aliexpress.by"),
}


def _marketplace_slug(value: str) -> str | None:
    normalized = value.casefold().replace("ё", "е")
    if "wildberries" in normalized or "вайлдберриз" in normalized:
        return "wildberries"
    if "aliexpress" in normalized or "алиэкспресс" in normalized:
        return "aliexpress"
    if "avito" in normalized or "авито" in normalized:
        return "avito"
    if "ozon" in normalized or "озон" in normalized:
        return "ozon"
    return None


def _direct_marketplace_url(value: object, marketplace: str) -> str | None:
    """Return a verified HTTPS product URL, unwrapping only explicit marketplace redirects."""
    slug = _marketplace_slug(marketplace)
    if slug is None:
        return None
    raw_url = str(value or "").strip()
    for _ in range(3):
        if not is_http_url(raw_url):
            return None
        parsed = urllib.parse.urlsplit(raw_url)
        if parsed.scheme.lower() != "https":
            return None
        host = (parsed.hostname or "").lower().removeprefix("www.")
        allowed = _DIRECT_MARKETPLACE_HOSTS[slug]
        if any(host == domain or host.endswith("." + domain) for domain in allowed):
            tracking_keys = {"erid", "subid", "ref", "refid", "affiliate", "affiliate_id", "click_id", "yclid", "gclid", "fbclid"}
            query_pairs = [
                (key, val) for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() not in tracking_keys and not key.casefold().startswith("utm_")
            ]
            safe_netloc = parsed.hostname.lower() if parsed.hostname else ""
            safe_query = urllib.parse.urlencode(query_pairs, doseq=True)
            return urllib.parse.urlunsplit(("https", safe_netloc, parsed.path or "/", safe_query, ""))
        if host in {"takprdm.ru", "www.takprdm.ru"}:
            redirect_targets = [
                val for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() in {"redirectto", "redirect_to", "target_url"}
            ]
            if not redirect_targets:
                return None
            raw_url = redirect_targets[0].strip()
            continue
        return None
    return None


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


def _describe_source_status(source: dict[str, Any]) -> str:
    source_id = source.get("id", "unknown")
    title = clean_text(source.get("title"), 100)
    status = clean_text(source.get("status") or "unknown", 40)
    created_at = clean_text(source.get("created_at"), 40)
    created_suffix = f" (created_at={created_at})" if created_at else ""
    return f"{source_id}:{title}={status}{created_suffix}"


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
            registered = ", ".join(_describe_source_status(source) for source in sources) or "no sources registered in this account"
            raise NoApprovedSourceError(
                f"TAKPRODAM_SOURCE_ID={expected} was not returned by the API. Registered sources: {registered}"
            )
        if str(match.get("status", "")).lower() != "approved":
            created_at = clean_text(match.get("created_at"), 40)
            created_note = f"; created_at={created_at}" if created_at else ""
            raise NoApprovedSourceError(
                f"Takprodam source {expected} is not approved yet "
                f"(status={match.get('status', 'unknown')}; title={clean_text(match.get('title'), 100)}{created_note}). "
                "Takprodam's publisher instructions say verification may take up to 48 hours. "
                "Confirm the verification code/text is present in the channel description; if the source "
                "is still pending more than 48 hours after creation, contact support@takprodam.ru. "
                "No products were fetched."
            )
        return expected

    if len(approved) == 1:
        return str(approved[0].get("id"))
    if not approved:
        registered = ", ".join(_describe_source_status(source) for source in sources) or "no sources registered in this account"
        raise NoApprovedSourceError(
            "No approved Takprodam publisher source found. Confirm the verification code/text is present "
            "in the channel description. Takprodam's publisher instructions say verification may take up to "
            "48 hours; if a source is still pending more than 48 hours after creation, contact "
            "support@takprodam.ru. API-reported sources: " + registered
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
    diagnostics: dict[str, int] | None = None,
) -> list[Deal]:
    """Map Takprodam/Admitad promotion-product items to the internal deal model."""
    deals: list[Deal] = []
    target_market = (marketplace_filter or "").strip().lower()

    def count(reason: str) -> None:
        if diagnostics is not None:
            diagnostics[reason] = diagnostics.get(reason, 0) + 1

    for item in items:
        count("received")
        if not isinstance(item, dict):
            count("rejected_invalid_item")
            continue
        marketplace = clean_text(item.get("marketplace_title") or item.get("marketplace") or "Маркетплейс", 100)
        if target_market and target_market not in marketplace.lower():
            count("rejected_marketplace_mismatch")
            continue

        title = clean_text(item.get("title"), 300)
        affiliate_url = str(item.get("tracking_link") or "").strip()
        if not title or not is_http_url(affiliate_url):
            count("rejected_missing_title_or_tracking_link")
            continue

        # Prefer a verified direct marketplace URL for diagnostics and referer.
        # In mixed mode, however, a valid partner tracking URL is itself a usable
        # offer destination when the API does not expose a clean merchant URL.
        # TelegramPublisher publishes affiliate_url for partner offers and only
        # uses product_url in explicit direct-link mode, so this fallback never
        # turns a tracking URL into a public/direct link.
        product_url = (
            _direct_marketplace_url(item.get("external_link"), marketplace)
            or _direct_marketplace_url(affiliate_url, marketplace)
            or affiliate_url
        )
        if not product_url:
            continue

        old_price = parse_price(item.get("price"))
        discounted_price = parse_price(item.get("price_discount"))
        reported_discount = _float_or_none(
            item.get("discount_percent", item.get("discount_percentage"))
        )

        # Some promotion products expose a verified discount percentage but omit
        # one or both price fields. Keep those offers as percentage-only deals;
        # never manufacture an "old price" from a percentage. A complete but
        # contradictory price pair remains a hard rejection.
        if old_price is not None and discounted_price is not None:
            if old_price <= discounted_price:
                count("rejected_invalid_discount_price_pair")
                continue
            price = discounted_price
            valid_old_price = old_price
            reported_discount = None
        elif reported_discount is not None and 0 < reported_discount <= 90:
            price = discounted_price
            valid_old_price = None
            count("accepted_percentage_only")
        else:
            count("rejected_missing_or_invalid_discount_price_pair")
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
                old_price=valid_old_price,
                affiliate_url=affiliate_url,
                reported_discount_percent=reported_discount,
                image_url=image_url,
                currency="RUB",
                description="",
                expires_at=None,
                external_id=str(item.get("product_id") or item.get("id") or "") or None,
                commission=commission,
                legal_text=clean_text(item.get("legal_text"), 500),
            )
        )
        count("accepted")
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
    diagnostics: dict[str, int] | None = None,
) -> list[Deal]:
    """Fetch approved-partner promotion products and their tracking links."""
    if not token.strip():
        raise ValueError("Takprodam API token is empty")
    if not str(source_id).strip():
        raise ValueError("Takprodam publisher source ID is empty")

    markets = marketplaces or ["Ozon", "Wildberries", "Avito", "Aliexpress"]
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
                        "marketplace": normalized.casefold(),
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
            found.extend(parse_promotion_items(normalized_items, normalized, diagnostics=diagnostics))
            if len(normalized_items) < page_limit:
                break
    return found


def fetch_configured_takprodam_deals(
    timeout: int = 25,
    diagnostics: dict[str, int] | None = None,
) -> tuple[list[Deal], str]:
    token = os.getenv("TAKPRODAM_API_TOKEN", "").strip()
    if not token:
        return [], ""

    configured_source_id = os.getenv("TAKPRODAM_SOURCE_ID", "").strip() or None
    source_id = resolve_source_id(token, configured_source_id, timeout=timeout)
    marketplace_setting = os.getenv("TAKPRODAM_MARKETPLACES", "Ozon,Wildberries,Avito,Aliexpress")
    marketplaces = [name.strip() for name in marketplace_setting.split(",") if name.strip()]
    limit = _env_int("TAKPRODAM_LIMIT", 1000)
    max_pages = _env_int("TAKPRODAM_MAX_PAGES", 1)
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
        diagnostics=diagnostics,
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

