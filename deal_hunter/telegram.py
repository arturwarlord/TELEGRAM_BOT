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

    notices: list[str] = []
    seen_notices: set[str] = set()
    for notice in (deal.legal_text, ad_notice):
        cleaned = str(notice or "").strip()
        if cleaned and cleaned.casefold() not in seen_notices:
            notices.append(cleaned)
            seen_notices.add(cleaned.casefold())
    notice_html = "\n".join(html.escape(notice) for notice in notices)
    if len(notice_html) > 900:
        raise ValueError("Partner/legal disclosure is too long for a Telegram photo caption; review this offer manually")

    def render(title_limit: int, include_promo: bool, include_expiry: bool) -> str:
        title = deal.title[:title_limit]
        if len(deal.title) > title_limit:
            title += "…"
        parts = [
            f"🔥 <b>ВЫГОДНАЯ НАХОДКА: −{discount:.0f}%</b>",
            "",
            f"<b>{html.escape(title)}</b>",
            "",
            f"❌ Было: <s>{_rubles(deal.old_price)}</s>",
            f"✅ Сейчас: <b>{_rubles(deal.price)}</b>",
            f"💰 Экономия: <b>{_rubles(savings)}</b>",
            f"🏪 Магазин: {html.escape(deal.store[:80])}",
        ]
        if include_promo and deal.promo_code:
            parts.append(f"🎟 Промокод: <code>{html.escape(deal.promo_code[:60])}</code>")
        if include_expiry and deal.expires_at:
            parts.append(f"⏳ До: {html.escape(deal.expires_at[:60])}")
        if notice_html:
            parts.extend(["", notice_html])
        return "\n".join(parts)

    # Keep partner-provided legal metadata intact; shorten marketing copy before
    # the disclosure rather than cutting off a legal label at the end.
    for limits in ((240, True, True), (100, True, True), (80, False, True), (80, False, False)):
        caption = render(*limits)
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
