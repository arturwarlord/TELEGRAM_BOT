from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_KEYS = {
    "fbclid", "gclid", "yclid", "ref", "refid", "from", "source",
    "affiliate", "affiliate_id", "aff_id", "click_id", "subid", "tag",
}


def clean_text(value: object, limit: int = 4000) -> str:
    """Convert a feed field or HTML fragment to readable plain text."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def is_http_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlsplit(value.strip())
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def canonical_url(value: str) -> str:
    """Normalize a product URL for deduplication without altering its published URL."""
    if not is_http_url(value):
        return ""
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.port and parsed.port not in (80, 443):
        host = f"{host}:{parsed.port}"
    filtered = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in _TRACKING_KEYS:
            continue
        filtered.append((key, val))
    query = urlencode(filtered, doseq=True)
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, query, ""))


def parse_price(value: object) -> float | None:
    """Parse a positive price from a feed field such as '1 299,00 RUB'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number > 0 else None

    text = html.unescape(str(value)).replace("\u00a0", " ").replace("\u202f", " ")
    match = re.search(r"(?<!\w)(\d[\d\s]*(?:[.,]\d{1,2})?)", text)
    if not match:
        return None
    raw = re.sub(r"\s+", "", match.group(1))
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[-1]
        if len(tail) == 3 and raw.count(",") == 1:
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    try:
        price = float(raw)
    except ValueError:
        return None
    return price if price > 0 else None


@dataclass(frozen=True)
class Deal:
    title: str
    product_url: str
    store: str = "Магазин"
    source: str = "unknown"
    price: float | None = None
    old_price: float | None = None
    affiliate_url: str | None = None
    image_url: str | None = None
    currency: str = "RUB"
    description: str = ""
    promo_code: str | None = None
    expires_at: str | None = None
    external_id: str | None = None

    @property
    def savings(self) -> float | None:
        if self.price is None or self.old_price is None or self.old_price <= self.price:
            return None
        return self.old_price - self.price

    @property
    def discount_percent(self) -> float | None:
        if self.price is None or self.old_price is None or self.old_price <= self.price:
            return None
        return (self.old_price - self.price) / self.old_price * 100

    @property
    def key(self) -> str:
        base = canonical_url(self.product_url)
        if not base:
            base = f"{self.store.lower()}|{self.title.lower().strip()}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]
