from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .models import Deal, clean_text, is_http_url, parse_price


DEFAULT_FIELDS: dict[str, list[str]] = {
    "title": ["title", "name", "product_name"],
    "product_url": ["product_url", "product_link", "url", "link", "href"],
    "affiliate_url": ["affiliate_url", "affiliate_link", "tracking_link", "deep_link", "partner_url"],
    "image_url": ["image_url", "image", "picture", "photo", "thumbnail"],
    "price": ["sale_price", "current_price", "price", "final_price", "salePrice"],
    "old_price": ["old_price", "oldprice", "regular_price", "list_price", "price_before_discount"],
    "currency": ["currency", "currency_code"],
    "description": ["description", "summary", "content", "text"],
    "store": ["store", "shop", "merchant", "retailer"],
    "promo_code": ["promo_code", "promocode", "coupon_code", "coupon"],
    "expires_at": ["expires_at", "expiry", "expiration_date", "valid_until", "end_date"],
    "external_id": ["guid", "id", "offer_id", "sku"],
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def _aliases(source: dict[str, Any], field: str) -> list[str]:
    configured = source.get("fields", {}).get(field)
    if isinstance(configured, str):
        return [configured.lower()]
    if isinstance(configured, list):
        return [str(x).lower() for x in configured]
    return [x.lower() for x in DEFAULT_FIELDS.get(field, [field])]


def _json_path(record: Any, path: str) -> Any:
    value = record
    for part in path.split("."):
        if isinstance(value, dict):
            if part in value:
                value = value[part]
            else:
                match = next((v for k, v in value.items() if str(k).lower() == part.lower()), None)
                value = match
        else:
            return None
        if value is None:
            return None
    return value


def _field(record: Any, source: dict[str, Any], field: str) -> str:
    aliases = _aliases(source, field)
    if isinstance(record, dict):
        for alias in aliases:
            value = _json_path(record, alias)
            if value is not None and not isinstance(value, (dict, list)):
                text = str(value).strip()
                if text:
                    return text
        return ""

    if isinstance(record, ET.Element):
        for node in record.iter():
            if _local_name(str(node.tag)) not in aliases:
                continue
            value = (node.text or "").strip()
            if not value:
                value = str(node.attrib.get("href") or node.attrib.get("url") or node.attrib.get("src") or "").strip()
            if value:
                return value
    return ""


def _extract_image(record: Any, description: str = "") -> str | None:
    if isinstance(record, ET.Element):
        for node in record.iter():
            tag = _local_name(str(node.tag))
            if tag in {"enclosure", "thumbnail", "picture", "image", "photo", "content"}:
                candidate = node.attrib.get("url") or node.attrib.get("href") or node.attrib.get("src")
                mime = str(node.attrib.get("type") or "").lower()
                if candidate and (tag in {"thumbnail", "picture", "image", "photo"} or not mime or mime.startswith("image/")):
                    if is_http_url(candidate):
                        return candidate.strip()
    html_text = str(description or "")
    match = re.search(r"""(?is)<img\b[^>]*?\bsrc=["']([^"']+)""", html_text)
    if match and is_http_url(match.group(1)):
        return match.group(1).strip()
    return None


def _infer_price_pair(text: str) -> tuple[float | None, float | None]:
    """Infer current/old prices only when the text signals a comparison."""
    pattern = re.compile(r"(?<!\w)(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:лей|ля|ль)?\.?|р\.)", re.IGNORECASE)
    matches = list(pattern.finditer(text or ""))
    snippet = (text or "").lower()
    old_markers = ("вместо", "везде", "обычно", "обычная цена", "было", "до скидки", "без скидки")
    if len(matches) >= 2 and any(marker in snippet for marker in old_markers):
        values = [parse_price(m.group(1)) for m in matches[:3]]
        values = [x for x in values if x is not None]
        if len(values) >= 2 and values[1] > values[0]:
            return values[0], values[1]
        if len(values) >= 2 and values[0] > values[1]:
            return values[1], values[0]

    # Handle a direct statement such as «с 2 000 ₽ до 1 000 ₽».
    direct = re.search(
        r"с\s*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|р\.)?\s*до\s*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|р\.)",
        snippet,
        re.IGNORECASE,
    )
    if direct:
        return parse_price(direct.group(2)), parse_price(direct.group(1))
    return None, None


def _record_to_deal(record: Any, source: dict[str, Any]) -> Deal | None:
    title = clean_text(_field(record, source, "title"), 300)
    product_url = _field(record, source, "product_url").strip()
    if not title or not is_http_url(product_url):
        return None

    raw_description = _field(record, source, "description")
    description = clean_text(raw_description, 1200)
    image = _field(record, source, "image_url").strip() or _extract_image(record, raw_description)
    if not is_http_url(image):
        image = None

    price = parse_price(_field(record, source, "price"))
    old_price = parse_price(_field(record, source, "old_price"))
    if price is None or old_price is None:
        inferred_price, inferred_old = _infer_price_pair(f"{title} {description}")
        if price is None:
            price = inferred_price
        if old_price is None:
            old_price = inferred_old

    affiliate_url = _field(record, source, "affiliate_url").strip()
    if not affiliate_url and source.get("links_are_affiliate") is True:
        affiliate_url = product_url
    if not is_http_url(affiliate_url):
        affiliate_url = None

    store = clean_text(_field(record, source, "store"), 100) or clean_text(source.get("store") or source.get("name") or "Магазин", 100)
    currency = clean_text(_field(record, source, "currency"), 12).upper() or str(source.get("currency") or "RUB").upper()
    promo_code = clean_text(_field(record, source, "promo_code"), 80) or None
    expires_at = clean_text(_field(record, source, "expires_at"), 80) or None
    external_id = clean_text(_field(record, source, "external_id"), 180) or None

    return Deal(
        title=title,
        product_url=product_url,
        store=store,
        source=str(source.get("name") or "unknown"),
        price=price,
        old_price=old_price,
        affiliate_url=affiliate_url,
        image_url=image,
        currency=currency,
        description=description,
        promo_code=promo_code,
        expires_at=expires_at,
        external_id=external_id,
    )


def parse_feed(payload: bytes, source: dict[str, Any], max_items: int = 200) -> list[Deal]:
    """Parse an RSS/Atom/XML catalogue or a JSON offer feed without network access."""
    feed_type = str(source.get("type", "rss")).lower()
    records: list[Any]

    if feed_type == "json":
        decoded = json.loads(payload.decode("utf-8-sig"))
        if isinstance(decoded, list):
            records = decoded
        elif isinstance(decoded, dict):
            items_path = str(source.get("items_path") or "items")
            items = _json_path(decoded, items_path)
            if not isinstance(items, list):
                for fallback in ("offers", "products", "data"):
                    items = decoded.get(fallback)
                    if isinstance(items, list):
                        break
            records = items if isinstance(items, list) else []
        else:
            records = []
    elif feed_type in {"rss", "xml", "atom"}:
        root = ET.fromstring(payload)
        record_tags = {str(x).lower() for x in source.get("record_tags", ["item", "entry", "offer", "product"])}
        records = [node for node in root.iter() if _local_name(str(node.tag)) in record_tags]
        if not records and _local_name(str(root.tag)) in record_tags:
            records = [root]
    else:
        raise ValueError(f"Unsupported feed type: {feed_type}")

    deals: list[Deal] = []
    for record in records[:max(1, max_items)]:
        try:
            deal = _record_to_deal(record, source)
        except (TypeError, ValueError, AttributeError):
            continue
        if deal is not None:
            deals.append(deal)
    return deals


def fetch_source(
    source: dict[str, Any],
    timeout: int = 20,
    max_bytes: int = 5 * 1024 * 1024,
    max_items: int = 200,
) -> list[Deal]:
    url = str(source.get("url") or "").strip()
    if not is_http_url(url):
        raise ValueError(f"Source '{source.get('name', 'unnamed')}' has an invalid URL")
    if source.get("enabled") is not True:
        return []

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DealHunterBot/0.1 (+RSS/XML/JSON partner feed reader)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json;q=0.9, */*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not fetch source '{source.get('name', 'unnamed')}': {exc}") from exc
    if len(payload) > max_bytes:
        raise ValueError(f"Source '{source.get('name', 'unnamed')}' exceeded the {max_bytes}-byte limit")
    return parse_feed(payload, source, max_items=max_items)
