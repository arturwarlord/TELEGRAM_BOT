from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from .models import Deal, is_http_url


@dataclass(frozen=True)
class DealDecision:
    eligible: bool
    reason: str
    discount_percent: float | None = None
    savings: float | None = None
    score: float = 0.0


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            return datetime.combine(parsed_date, time.max, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        # Unknown date formats are not used to claim an expiry.
        return None


def evaluate_deal(
    deal: Deal,
    *,
    min_discount_percent: float = 30.0,
    min_savings: float = 100.0,
    max_discount_percent: float = 90.0,
    min_coupon_discount_percent: float = 10.0,
    require_affiliate: bool = True,
    require_image: bool = True,
    now: datetime | None = None,
) -> DealDecision:
    """Validate either a price-comparison deal or an explicit coupon offer."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    if not is_http_url(deal.product_url):
        return DealDecision(False, "invalid product URL")
    if deal.currency.upper() not in {"RUB", "RUR", "₽"}:
        return DealDecision(False, f"unsupported currency: {deal.currency}")

    expiry = _parse_expiry(deal.expires_at)
    if expiry and expiry < current_time.astimezone(timezone.utc):
        return DealDecision(False, "offer expired")

    has_price_pair = deal.price is not None and deal.old_price is not None
    if not has_price_pair and (deal.price is not None or deal.old_price is not None):
        # Keep the specific diagnostic for an incomplete price comparison unless
        # the source also supplies a coupon/discount that can stand on its own.
        if not deal.promo_code and deal.reported_discount_percent is None:
            return DealDecision(False, "missing current or old price")
    if has_price_pair:
        if deal.price <= 0:
            return DealDecision(False, "current price must be positive")
        if deal.old_price <= deal.price:
            return DealDecision(False, "old price is not higher than current price")

        discount = deal.discount_percent
        savings = deal.savings
        if discount is None or savings is None:
            return DealDecision(False, "could not calculate savings")
        if discount < min_discount_percent:
            return DealDecision(False, f"discount below threshold ({discount:.1f}%)", discount, savings)
        if savings < min_savings:
            return DealDecision(False, f"savings below threshold ({savings:.0f} RUB)", discount, savings)
        if discount > max_discount_percent:
            return DealDecision(False, f"suspicious discount ({discount:.1f}%)", discount, savings)

        if require_affiliate and not is_http_url(deal.affiliate_url):
            return DealDecision(False, "missing approved affiliate URL", discount, savings)
        if require_image and not is_http_url(deal.image_url):
            return DealDecision(False, "missing product image URL", discount, savings)

        score = min(100.0, discount * 1.2 + min(savings / 500.0, 20.0))
        return DealDecision(True, "eligible", discount, savings, score)

    # Coupon/promotional cards often state a working code or discount but don't
    # expose two comparable prices. Accept only an explicit code or a percentage
    # actually printed by the source; never synthesize a "was / now" price.
    discount = deal.reported_discount_percent
    if not deal.promo_code and discount is None:
        return DealDecision(False, "missing price pair or explicit coupon/discount")
    if discount is not None:
        if discount < min_coupon_discount_percent:
            return DealDecision(False, f"coupon discount below threshold ({discount:.1f}%)", discount, None)
        if discount > max_discount_percent:
            return DealDecision(False, f"suspicious coupon discount ({discount:.1f}%)", discount, None)

    if require_affiliate and not is_http_url(deal.affiliate_url):
        return DealDecision(False, "missing approved affiliate URL", discount, None)
    if require_image and not is_http_url(deal.image_url):
        return DealDecision(False, "missing product image URL", discount, None)

    score = min(100.0, discount * 1.2) if discount is not None else 5.0
    return DealDecision(True, "eligible coupon", discount, None, score)
