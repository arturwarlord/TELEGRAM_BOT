from __future__ import annotations

import html
import json
import urllib.error
import urllib.request

from .models import Deal, is_http_url
from .scoring import DealDecision


def _rubles(value: float | None) -> str:
    if value is None:
        return "—"
    amount = f"{value:,.0f}".replace(",", " ")
    return f"{amount} ₽"


def format_caption(deal: Deal, decision: DealDecision, ad_notice: str) -> str:
    discount = decision.discount_percent
    savings = decision.savings
    coupon_only = deal.price is None or deal.old_price is None

    notices: list[str] = []
    seen_notices: set[str] = set()
    for notice in (deal.legal_text, ad_notice):
        cleaned = str(notice or "").strip()
        if cleaned and cleaned.casefold() not in seen_notices:
            notices.append(cleaned)
            seen_notices.add(cleaned.casefold())
    notice_html = "\n".join(html.escape(notice) for notice in notices)
    if len(notice_html) > 900:
        raise ValueError("Post disclosure/legal text is too long for a Telegram photo caption; review this offer manually")

    def render(title_limit: int, include_description: bool, include_promo: bool, include_expiry: bool) -> str:
        title = deal.title[:title_limit]
        if len(deal.title) > title_limit:
            title += "…"

        if coupon_only:
            if discount is not None and deal.discount_is_maximum:
                headline = f"🎟 <b>АКЦИЯ: скидка до {discount:.0f}%</b>"
            elif discount is not None:
                headline = f"🎟 <b>ВЫГОДНАЯ АКЦИЯ: скидка {discount:.0f}%</b>"
            else:
                headline = "🎟 <b>ПРОМОКОД / СПЕЦПРЕДЛОЖЕНИЕ</b>"
            parts = [headline, "", f"<b>{html.escape(title)}</b>"]
            if include_description and deal.description:
                description = deal.description[:320].strip()
                if description and description.casefold() != title.casefold():
                    parts.extend(["", html.escape(description)])
            parts.extend(["", f"🏷 Источник: {html.escape(deal.store[:80])}"])
        else:
            shown_discount = discount or 0.0
            shown_savings = savings or 0.0
            parts = [
                f"🔥 <b>ВЫГОДНАЯ НАХОДКА: −{shown_discount:.0f}%</b>",
                "",
                f"<b>{html.escape(title)}</b>",
                "",
                f"❌ Было: <s>{_rubles(deal.old_price)}</s>",
                f"✅ Сейчас: <b>{_rubles(deal.price)}</b>",
                f"💰 Экономия: <b>{_rubles(shown_savings)}</b>",
                f"🏪 Магазин / источник: {html.escape(deal.store[:80])}",
            ]
        if include_promo and deal.promo_code:
            parts.append(f"🎟 Промокод: <code>{html.escape(deal.promo_code[:60])}</code>")
        if include_expiry and deal.expires_at:
            parts.append(f"⏳ Условия / срок: {html.escape(deal.expires_at[:60])}")
        if notice_html:
            parts.extend(["", notice_html])
        return "\n".join(parts)

    # Preserve source/legal disclosures and shorten descriptive copy first.
    for options in (
        (240, True, True, True),
        (120, True, True, True),
        (80, False, True, True),
        (80, False, True, False),
    ):
        caption = render(*options)
        if len(caption) <= 1024:
            return caption
    raise ValueError("Caption does not fit Telegram's 1024-character photo caption limit; review this offer manually")

class TelegramPublisher:
    def __init__(self, token: str, chat_id: str, timeout: int = 25) -> None:
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.timeout = timeout
        if not self.token or not self.chat_id:
            raise ValueError("Telegram bot token and chat ID are required")

    def publish(
        self,
        deal: Deal,
        decision: DealDecision,
        ad_notice: str,
        *,
        allow_direct_link: bool = False,
    ) -> int | None:
        if not decision.eligible:
            raise ValueError(f"Refusing to publish an ineligible deal: {decision.reason}")
        # When direct-link mode is enabled, deliberately ignore all affiliate URLs,
        # including links supplied by third-party feeds. The button always goes to
        # the retailer's product page so this mode does not claim/track commissions.
        target_url = deal.product_url if allow_direct_link else deal.affiliate_url
        if not is_http_url(target_url):
            reason = "a valid direct product URL" if allow_direct_link else "an approved affiliate URL"
            raise ValueError(f"Refusing to publish without {reason}")
        if not is_http_url(deal.image_url):
            raise ValueError("Refusing to publish without a product image URL")
        if not ad_notice.strip():
            raise ValueError("A post disclosure/notice is required before publication")

        caption = format_caption(deal, decision, ad_notice)
        button_text = "🛍 Открыть предложение" if allow_direct_link else "🔥 Забрать скидку"
        payload = {
            "chat_id": self.chat_id,
            "photo": deal.image_url,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": button_text, "url": target_url}
                ]]
            },
        }
        endpoint = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not include the request URL: it contains the secret bot token.
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not publish to Telegram: {exc}") from exc

        if not result.get("ok"):
            description = str(result.get("description") or "unknown Telegram API error")
            raise RuntimeError(f"Telegram API error: {description}")
        message = result.get("result") or {}
        return message.get("message_id")
