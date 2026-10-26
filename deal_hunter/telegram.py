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
    discount = decision.discount_percent or 0.0
    savings = decision.savings or 0.0
    parts = [
        f"🔥 <b>ВЫГОДНАЯ НАХОДКА: −{discount:.0f}%</b>",
        "",
        f"<b>{html.escape(deal.title[:240])}</b>",
        "",
        f"❌ Было: <s>{_rubles(deal.old_price)}</s>",
        f"✅ Сейчас: <b>{_rubles(deal.price)}</b>",
        f"💰 Экономия: <b>{_rubles(savings)}</b>",
        f"🏪 Магазин: {html.escape(deal.store[:100])}",
    ]
    if deal.promo_code:
        parts.append(f"🎟 Промокод: <code>{html.escape(deal.promo_code[:80])}</code>")
    if deal.expires_at:
        parts.append(f"⏳ До: {html.escape(deal.expires_at[:80])}")
    if ad_notice.strip():
        parts.extend(["", html.escape(ad_notice.strip()[:300])])
    caption = "\n".join(parts)
    # Telegram photo captions are limited to 1024 characters after parsing.
    if len(caption) > 1000:
        short_title = html.escape(deal.title[:100]) + "…"
        parts[2] = f"<b>{short_title}</b>"
        caption = "\n".join(parts)
    return caption[:1024]


class TelegramPublisher:
    def __init__(self, token: str, chat_id: str, timeout: int = 25) -> None:
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.timeout = timeout
        if not self.token or not self.chat_id:
            raise ValueError("Telegram bot token and chat ID are required")

    def publish(self, deal: Deal, decision: DealDecision, ad_notice: str) -> int | None:
        if not decision.eligible:
            raise ValueError(f"Refusing to publish an ineligible deal: {decision.reason}")
        if not is_http_url(deal.affiliate_url):
            raise ValueError("Refusing to publish without an approved affiliate URL")
        if not is_http_url(deal.image_url):
            raise ValueError("Refusing to publish without a product image URL")
        if not ad_notice.strip():
            raise ValueError("AD_NOTICE is required before commercial posts can be published")

        caption = format_caption(deal, decision, ad_notice)
        payload = {
            "chat_id": self.chat_id,
            "photo": deal.image_url,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "🔥 Забрать скидку", "url": deal.affiliate_url}
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
