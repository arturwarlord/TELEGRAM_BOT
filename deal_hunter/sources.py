from __future__ import annotations

import json
import re
from datetime import date
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
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



def _extract_reported_percent(text: str) -> tuple[float | None, bool]:
    """Read a percentage only when the visible text explicitly ties it to an offer."""
    normalized = clean_text(text, 2000)
    patterns = [
        # Catch formats like SocialKod's "до − 90 %" as a maximum, not an exact offer.
        (r"\bдо\s*[−-]?\s*(\d{1,2}(?:[.,]\d+)?)\s*%", True),
        (r"скидк\w*\s+до\s*(\d{1,2}(?:[.,]\d+)?)\s*%", True),
        (r"скидк\w*[^%\d]{0,16}(\d{1,2}(?:[.,]\d+)?)\s*%", False),
        (r"[−-]\s*(\d{1,2}(?:[.,]\d+)?)\s*%", False),
        (r"\(\s*(\d{1,2}(?:[.,]\d+)?)\s*%\s*\)", False),
        (r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:скидк\w*|на\s+(?:все|весь|любые|первый|повторный)\s+\w+)", False),
    ]
    for pattern, is_maximum in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if 0 < value <= 100:
            return value, is_maximum
    return None, False


def _extract_promo_code(node: _HtmlNode, text: str) -> str | None:
    stopwords = {
        "ПРОМОКОД", "ПРОМОКОДОМ", "СКОПИРОВАТЬ", "ПОЛУЧИТЬ", "ПРИМЕНИТЬ",
        "СКИДКА", "СКИДКИ", "ОНЛАЙН", "КАССЕ", "ТОВАРЫ", "МАГАЗИН", "АКЦИЯ",
        "АКЦИИ", "ДОСТАВКА", "НОВЫЙ", "ЗАКАЗ", "ДО", "РЕКЛАМА", "БЕСПЛАТНО",
    }

    def valid(value: object) -> str | None:
        candidate = clean_text(value, 60).strip().strip("\"'")
        if not candidate or len(candidate) < 3 or len(candidate) > 32:
            return None
        if any(ch.isspace() for ch in candidate):
            return None
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9_-]+", candidate):
            return None
        if candidate.upper() in stopwords or candidate.isdigit():
            return None
        if candidate != candidate.upper():
            return None
        return candidate

    for child in _html_walk(node):
        for attr in ("data-promo-code", "data-coupon", "data-code", "data-promocode"):
            value = valid(child.attrs.get(attr))
            if value:
                return value
        class_name = child.attrs.get("class", "").lower()
        if child.tag in {"code", "kbd"} or any(part in class_name for part in ("promo-code", "coupon-code", "promocode", "coupon__code")):
            value = valid(_html_text(child))
            if value:
                return value

    for match in re.finditer(r"\b(?:промокод|код)\b\s*[:№#-]\s*([^\s,.;:]+)", text, flags=re.IGNORECASE):
        value = valid(match.group(1))
        if value:
            return value

    if re.search(r"скопировать\s+промокод|получить\s+код|товары\s+с\s+промокодом|используйте\s+код", text, flags=re.IGNORECASE):
        copied_match = re.search(r"скопировать\s+промокод|получить\s+код|товары\s+с\s+промокодом|используйте\s+код", text, flags=re.IGNORECASE)
        max_position = copied_match.start() if copied_match else len(text)
        percent_match = re.search(r"[−-]?\s*\d{1,2}\s*%", text)
        min_position = percent_match.end() if percent_match else 0
        for match in re.finditer(r"(?<![\w])([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9_-]{2,23})(?![\w])", text):
            if min_position <= match.start() < max_position:
                value = valid(match.group(1))
                if value:
                    return value
    return None


def _extract_promo_expiry(text: str) -> str | None:
    match = re.search(r"\bдо\s+(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b", text, flags=re.IGNORECASE)
    if match:
        day, month, year = (int(match.group(i)) for i in (1, 2, 3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    month_numbers = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    match = re.search(
        r"\bдо\s+(?:(\d{1,2})\s+)?(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+(\d{4}))?\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    day = int(match.group(1) or "1")
    month = month_numbers[match.group(2).lower()]
    year = int(match.group(3) or datetime.now(timezone.utc).year)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_offer_description(text: str) -> str:
    """Keep offer conditions but strip CTA/tracking/legal footer noise from card copy."""
    value = clean_text(text, 1400)
    # Do not copy a coupon site's own advertising label/ERID as if it applied to
    # this channel's post, and avoid CTA text that exposes tracking destinations.
    cut_markers = (
        "перейти на сайт", "перейти в магазин", "в магазин", "скопировать промокод",
        "скопировать", "копировать", "показать код", "открыть акцию",
        "реклама.", "erid:", "erid ",
    )
    positions = [value.lower().find(marker) for marker in cut_markers if value.lower().find(marker) >= 0]
    if positions:
        value = value[:min(positions)].strip()
    return value[:700]


def parse_html_promotions(payload: bytes, source: dict[str, Any], max_items: int = 200) -> list[Deal]:
    """Parse public HTML offer cards that publish a promo code or an explicit percentage.

    This parser never invents old/current prices. It uses the source's stated
    percentage (if any) and preserves coupon terms so posts can be useful before
    affiliate links are available.
    """
    base_url = str(source.get("url") or "").strip()
    parser = _HtmlTreeParser()
    parser.feed(payload.decode("utf-8-sig", errors="replace"))
    parser.close()
    nodes = list(_html_walk(parser.root))

    text_by_id: dict[int, str] = {}
    ignored = {"script", "style", "noscript", "svg"}
    for node in reversed(nodes):
        parts: list[str] = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag not in ignored:
                parts.append(text_by_id.get(id(child), ""))
        text_by_id[id(node)] = clean_text(" ".join(parts), 5000)

    deals: list[Deal] = []
    seen: set[str] = set()
    for node in reversed(nodes):
        if node.tag in {"document", "html", "body", "main", "script", "style", "nav", "footer", "header"}:
            continue
        text = text_by_id.get(id(node), "")
        if len(text) < 25 or len(text) > 1400:
            continue

        reported_percent, is_maximum = _extract_reported_percent(text)
        code = _extract_promo_code(node, text)
        if reported_percent is None and code is None:
            continue

        all_links = _html_links(node, base_url)
        base_host = (urlsplit(base_url).hostname or "").lower().removeprefix("www.")
        links: list[tuple[str, str]] = []
        for href, label in all_links:
            parsed = urlsplit(href)
            host = (parsed.hostname or "").lower().removeprefix("www.")
            path = parsed.path.lower()
            query = parsed.query.lower()
            # The goal in no-affiliate mode is a direct page on the coupon source,
            # not an affiliate redirect to a retailer (often marked by erid/adj/prfl).
            if host != base_host:
                continue
            if href.rstrip("/") == base_url.rstrip("/"):
                continue
            if any(marker in path for marker in (
                "/category/", "/categories/", "/profile/", "/account/", "/personal/",
                "/login/", "/signin/", "/cart/", "/checkout/", "/okgo/", "/redirect/",
                "/out/", "/click/", "/clickout/", "/tracking/", "/track/",
                "/api/click-product/", "/go/", "/blog/",
            )):
                continue
            if any(key in query for key in (
                "erid=", "affiliate=", "aff_id=", "subid=", "click_id=",
                "adj_", "prfl", "utm_source=",
            )):
                continue
            if _is_generic_link_label(label):
                continue
            links.append((href, label))

        images = _html_images(node, base_url)
        if not links or not images:
            continue

        def link_priority(link: tuple[str, str]) -> tuple[int, int]:
            path = urlsplit(link[0]).path.lower()
            if any(segment in path for segment in ("/promo/", "/offer/", "/coupon/", "/discount/", "/deal/", "/deals/", "/action/")):
                priority = 3
            elif any(segment in path for segment in ("/store/", "/shop/", "/shops/")):
                priority = 2
            else:
                priority = 1
            return priority, min(len(link[1]), 300)

        href, title = max(links, key=link_priority)
        title = clean_text(title, 300)
        if not title or len(title) < 4:
            title = next(
                (clean_text(child.attrs.get("alt"), 300) for child in _html_walk(node)
                 if child.tag == "img" and len(clean_text(child.attrs.get("alt"), 300)) >= 4),
                "",
            )
        if not title:
            title = f"Промокод {code}" if code else "Акция со скидкой"

        parsed_offer_link = urlsplit(href)
        offer_path = parsed_offer_link.path.casefold()
        offer_fragment = parsed_offer_link.fragment.casefold()
        is_store_index_path = any(
            marker in offer_path
            for marker in ("/store/", "/shop/", "/shops/", "/magazin/", "/promokody/")
        )
        is_generic_store_title = bool(re.search(
            r"(?:^\s*все\s+(?:промокоды|купоны)\b|"
            r"\b\d+\s+(?:купон\w*|товар\w*)\b|"
            r"^\s*от\s+\w+|↗\s*$)",
            title,
            flags=re.IGNORECASE,
        ))
        has_offer_detail_path = any(
            marker in offer_path
            for marker in ("/promo/", "/promocodes/", "/coupon/", "/discount/", "/deal/", "/deals/", "/action/", "/shares/")
        )
        has_offer_anchor = bool(re.search(
            r"(?:promo|offer|coupon|deal|discount|action|stock)[-_]?\d+",
            offer_fragment,
            flags=re.IGNORECASE,
        ))
        # Store directories can contain unrelated codes/percentages from neighbouring
        # cards. Only keep a generic store-title when its link identifies an individual
        # promo/offer (including anchors such as #promo-1461).
        if (
            is_store_index_path
            and is_generic_store_title
            and not has_offer_detail_path
            and not has_offer_anchor
        ):
            continue

        description = _extract_offer_description(text)
        # A percent picked up from a broad parent node is not enough on its own.
        # Re-check against the selected offer title and trimmed card copy so an
        # unrelated "up to 80%" from a neighbouring card cannot become this deal's claim.
        local_offer_text = f"{title} {description[:240]}"
        local_percent, local_is_maximum = _extract_reported_percent(local_offer_text)
        if local_percent is None:
            reported_percent = None
            is_maximum = False
        else:
            reported_percent = local_percent
            is_maximum = local_is_maximum

        # Avoid attaching a neighbouring card's "up to 80%" to a loyalty-points
        # or bonus offer. For points/cashback titles, only trust a percentage when
        # the title itself describes a discount; broad parent text is not enough.
        value_terms_in_title = re.search(
            r"\b(?:балл\w*|бонус\w*|кешбэк|кэшбэк|кеш[- ]?бек|кэш[- ]?бек|cashback|вау)\b",
            title,
            flags=re.IGNORECASE,
        )
        title_explicitly_describes_discount = bool(re.search(
            r"(?:скидк\w*[^%\d]{0,16}\d{1,2}(?:[.,]\d+)?\s*%|"
            r"[−-]\s*\d{1,2}(?:[.,]\d+)?\s*%|"
            r"\d{1,2}(?:[.,]\d+)?\s*%\s*(?:скидк\w*|на\b))",
            title,
            flags=re.IGNORECASE,
        ))
        if value_terms_in_title and not title_explicitly_describes_discount:
            reported_percent = None
            is_maximum = False
        if reported_percent is None and code is None:
            continue

        deal = Deal(
            title=title,
            product_url=href,
            store=str(source.get("store") or source.get("name") or "Промокоды"),
            source=str(source.get("name") or "public-html-promotions"),
            affiliate_url=None,
            image_url=images[0],
            currency=str(source.get("currency") or "RUB").upper(),
            description=description,
            promo_code=code,
            expires_at=_extract_promo_expiry(text),
            reported_discount_percent=reported_percent,
            discount_is_maximum=is_maximum,
        )
        if deal.key not in seen:
            deals.append(deal)
            seen.add(deal.key)
            if len(deals) >= max(1, max_items):
                break
    return deals


def parse_feed(payload: bytes, source: dict[str, Any], max_items: int = 200) -> list[Deal]:
    """Parse an RSS/Atom/XML catalogue or a JSON offer feed without network access."""
    feed_type = str(source.get("type", "rss")).lower()
    records: list[Any]

    if feed_type == "html":
        return parse_html_catalog(payload, source, max_items=max_items)
    if feed_type in {"html_promos", "html-coupons"}:
        return parse_html_promotions(payload, source, max_items=max_items)

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

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


class _HtmlNode:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_HtmlNode | None" = None):
        self.tag = tag
        self.attrs = attrs
        self.children: list["_HtmlNode | str"] = []
        self.parent = parent


class _HtmlTreeParser(HTMLParser):
    """Small stdlib-only DOM builder for public, server-rendered sale pages."""

    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _HtmlNode(tag.lower(), {str(k).lower(): str(v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _HtmlNode(tag.lower(), {str(k).lower(): str(v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        target = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == target:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def _html_walk(node: _HtmlNode):
    yield node
    for child in node.children:
        if isinstance(child, _HtmlNode):
            yield from _html_walk(child)


def _html_text(node: _HtmlNode) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in {"script", "style", "noscript", "svg"}:
            parts.append(_html_text(child))
    return clean_text(" ".join(parts), 5000)


def _html_links(node: _HtmlNode, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for child in _html_walk(node):
        if child.tag != "a":
            continue
        href = urljoin(base_url, child.attrs.get("href", "").strip())
        label = _html_text(child)
        if is_http_url(href) and label:
            links.append((href, label))
    return links


def _html_images(node: _HtmlNode, base_url: str) -> list[str]:
    images: list[str] = []
    for child in _html_walk(node):
        if child.tag != "img":
            continue
        value = (
            child.attrs.get("src")
            or child.attrs.get("data-src")
            or child.attrs.get("data-original")
            or child.attrs.get("data-lazy-src")
            or ""
        ).strip()
        if not value:
            srcset = child.attrs.get("srcset", "").strip()
            if srcset:
                value = srcset.split(",")[0].strip().split(" ")[0]
        candidate = urljoin(base_url, value)
        if is_http_url(candidate) and candidate not in images:
            images.append(candidate)
    return images


_PRICE_TOKEN = r"(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:лей|ля|ль)?\.?|р\.)"


def _html_price_pair(text: str) -> tuple[float | None, float | None]:
    normalized = clean_text(text, 5000)
    current: float | None = None
    old: float | None = None

    # Common retailer labels, including Kuper's "Цена без скидки / Цена со скидкой".
    old_patterns = [
        rf"(?:старая цена|цена без скидки|обычная цена|цена до скидки|было)\b.{{0,90}}?{_PRICE_TOKEN}",
    ]
    for pattern in old_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            old = parse_price(match.group(1))
            break

    # First look for explicitly discounted price labels. These often include
    # unit text ("за 1 шт." / "за 1 кг") before the amount.
    discounted = re.search(rf"цена со скидкой\b.{{0,90}}?{_PRICE_TOKEN}", normalized, flags=re.IGNORECASE)
    if discounted:
        current = parse_price(discounted.group(1))

    # Otherwise use a generic "Цена N ₽" occurrence but skip old/regular-price labels.
    if current is None:
        for match in re.finditer(rf"\bцена\b.{{0,60}}?{_PRICE_TOKEN}", normalized, flags=re.IGNORECASE):
            prefix = normalized[max(0, match.start() - 24):match.start()].casefold()
            label_window = normalized[match.start():min(len(normalized), match.start() + 35)].casefold()
            if any(marker in prefix for marker in ("старая ", "обычная ")) or "без скидки" in label_window or "до скидки" in label_window:
                continue
            candidate = parse_price(match.group(1))
            if candidate is not None and (old is None or candidate < old):
                current = candidate
                break

    # If explicit labels are absent, only accept two prices when the card clearly
    # shows a discount marker (e.g. "-35%") and the values are ordered.
    if current is None or old is None:
        if re.search(r"(?:-\s*|\(\s*)\d{1,2}\s*%(?:\s*\))?", normalized):
            values = [
                parse_price(m.group(1))
                for m in re.finditer(_PRICE_TOKEN, normalized, flags=re.IGNORECASE)
            ]
            values = [value for value in values if value is not None]
            if len(values) >= 2:
                low, high = min(values[:2]), max(values[:2])
                if high > low:
                    current, old = low, high
    if current is None or old is None or old <= current:
        return None, None
    return current, old


def _is_generic_link_label(label: str) -> bool:
    normalized = clean_text(label, 250).casefold()
    generic = {
        "подробнее", "купить", "в корзину", "все товары", "ещё", "еще",
        "показать все", "смотреть все", "перейти", "перейти на сайт",
        "перейти в магазин", "открыть акцию", "показать код",
        "скопировать", "копировать", "акции", "скидки", "каталог",
        "главная", "войти", "избранное",
    }
    return not normalized or normalized in generic or len(normalized) < 5


def _parse_jsonld_products(parser: _HtmlTreeParser, source: dict[str, Any], base_url: str) -> list[Deal]:
    deals: list[Deal] = []
    for node in _html_walk(parser.root):
        if node.tag != "script" or "ld+json" not in node.attrs.get("type", "").lower():
            continue
        raw = "".join(child for child in node.children if isinstance(child, str)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        def visit(value: Any):
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return
            kind = value.get("@type", [])
            types = [kind] if isinstance(kind, str) else kind if isinstance(kind, list) else []
            if any(str(item).lower() == "product" for item in types):
                offers = value.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price = parse_price(offers.get("price") or offers.get("lowPrice"))
                    price_spec = offers.get("priceSpecification")
                    old_price_value = (
                        value.get("highPrice")
                        or offers.get("highPrice")
                        or offers.get("listPrice")
                        or (price_spec.get("price") if isinstance(price_spec, dict) else None)
                    )
                    old_price = parse_price(old_price_value)
                    name = clean_text(value.get("name"), 300)
                    product_url = urljoin(base_url, str(value.get("url") or offers.get("url") or "").strip())
                    image = value.get("image")
                    if isinstance(image, list):
                        image = image[0] if image else None
                    image_url = urljoin(base_url, str(image or "").strip())
                    if (
                        name and is_http_url(product_url) and is_http_url(image_url)
                        and price is not None and old_price is not None and old_price > price
                    ):
                        deals.append(Deal(
                            title=name,
                            product_url=product_url,
                            store=str(source.get("store") or source.get("name") or "Магазин"),
                            source=str(source.get("name") or "public-html"),
                            price=price,
                            old_price=old_price,
                            image_url=image_url,
                            currency=str(offers.get("priceCurrency") or source.get("currency") or "RUB").upper(),
                            external_id=str(value.get("sku") or value.get("productID") or "") or None,
                            description=clean_text(value.get("description"), 800),
                        ))
            for child_value in value.values():
                if isinstance(child_value, (dict, list)):
                    visit(child_value)

        visit(data)
    return deals


def parse_html_catalog(payload: bytes, source: dict[str, Any], max_items: int = 200) -> list[Deal]:
    """Extract only explicit old/current price pairs from a public store catalogue page.

    No pages are bypassed and no prices are guessed from separate pages. If the site
    renders prices only after login/location selection, it will simply produce no deals.
    """
    base_url = str(source.get("url") or "").strip()
    parser = _HtmlTreeParser()
    parser.feed(payload.decode("utf-8-sig", errors="replace"))
    parser.close()

    deals = _parse_jsonld_products(parser, source, base_url)
    seen = {deal.key for deal in deals}
    nodes = list(_html_walk(parser.root))
    # Smallest elements are visited first, making repeated outer containers harmless.
    for node in reversed(nodes):
        if node.tag in {"document", "html", "body", "main", "script", "style", "nav", "footer", "header"}:
            continue
        text = _html_text(node)
        if len(text) < 30 or len(text) > 2200:
            continue
        current_price, old_price = _html_price_pair(text)
        if current_price is None or old_price is None:
            continue

        links = _html_links(node, base_url)
        images = _html_images(node, base_url)
        if not links or not images:
            continue
        # Prefer a plausible product link with a product name, not navigation or category.
        candidates = [
            (href, label) for href, label in links
            if not _is_generic_link_label(label)
            and href.rstrip("/") != base_url.rstrip("/")
            and not re.search(r"/(promotions?|actions?|offers?|personaloffers|sp|catalog)(/|$)", urlsplit(href).path, re.I)
        ]
        if not candidates:
            continue
        href, title = max(candidates, key=lambda pair: min(len(pair[1]), 300))
        title = clean_text(title, 300)
        if len(title) < 5:
            # Sometimes the title is only available as an image alt attribute.
            title = next(
                (clean_text(child.attrs.get("alt"), 300) for child in _html_walk(node)
                 if child.tag == "img" and len(clean_text(child.attrs.get("alt"), 300)) >= 5),
                "",
            )
        if not title or len(title) < 5:
            continue

        deal = Deal(
            title=title,
            product_url=href,
            store=str(source.get("store") or source.get("name") or "Магазин"),
            source=str(source.get("name") or "public-html"),
            price=current_price,
            old_price=old_price,
            image_url=images[0],
            currency=str(source.get("currency") or "RUB").upper(),
            description=text[:800],
        )
        if deal.key not in seen:
            deals.append(deal)
            seen.add(deal.key)
            if len(deals) >= max(1, max_items):
                break
    return deals

