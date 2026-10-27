from __future__ import annotations

import html
import json
import secrets
import urllib.error
import urllib.request

from .models import Deal, is_http_url
from .scoring import DealDecision


MAX_PHOTO_BYTES = 10 * 1024 * 1024


def _rubles(value: float | None) -> str:
    if value is None:
        return "—"
    amount = f"{value:,.0f}".replace(",", " ")
    return amount + " ₽"


def _detect_image_format(data: bytes) -> tuple[str, str] | None:
    """Return (MIME type, filename) only for recognised image signatures."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "offer.jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "offer.png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "offer.gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "offer.webp"
    return None


def _download_photo(url: str, referer: str, timeout: int) -> tuple[bytes, str, str]:
    """Download and validate the source image in the runner before uploading to Telegram."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DealHunterBot/1.0; +https://github.com/arturwarlord/TELEGRAM_BOT)",
            "Accept": "image/avif,image/webp,image/apng,image/png,image/jpeg,image/gif,*/*;q=0.8",
            "Referer": referer,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_PHOTO_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Could not download offer image (HTTP {exc.code})") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not download offer image: network error ({type(exc.reason).__name__})") from None
    except TimeoutError:
        raise RuntimeError("Could not download offer image: request timed out") from None

    if not data:
        raise RuntimeError("Offer image download returned an empty file")
    if len(data) > MAX_PHOTO_BYTES:
        raise RuntimeError("Offer image is larger than Telegram's 10 MiB upload limit")
    detected = _detect_image_format(data)
    if detected is None:
        raise RuntimeError("Offer image URL did not return a supported JPEG, PNG, GIF, or WebP image")
    mime_type, filename = detected
    return data, mime_type, filename


def _multipart_form(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    mime_type: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    """Build a UTF-8 multipart/form-data request without third-party packages."""
    boundary = "----DealHunter" + secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
            value.encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode("ascii"),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("ascii"),
        f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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

        if deal.offer_type == "flight":
            parts = [
                "✈️ <b>ВЫГОДНЫЕ АВИАБИЛЕТЫ</b>", "",
                f"<b>{html.escape(title)}</b>", "",
                f"💳 Цена в предложении: <b>{_rubles(deal.price)}</b>",
            ]
            if include_description and deal.description:
                description = deal.description[:320].strip()
                if description:
                    parts.extend(["", html.escape(description)])
            if include_expiry and deal.expires_at:
                parts.append(f"⏱ Срок цены в источнике: {html.escape(deal.expires_at[:60])}")
            parts.extend([
                "", "Цена и наличие могут измениться — проверьте итоговую стоимость на Aviasales.",
                f"🔎 Источник: {html.escape(deal.store[:80])}",
            ])
        elif coupon_only:
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
        # Non-affiliate mode deliberately ignores tracking URLs. The public offer URL
        # may be a merchant page or a coupon/catalog detail page, depending on source.
        target_url = deal.product_url if allow_direct_link else deal.affiliate_url
        if not is_http_url(target_url):
            reason = "a valid direct offer URL" if allow_direct_link else "an approved affiliate URL"
            raise ValueError(f"Refusing to publish without {reason}")
        if not is_http_url(deal.image_url):
            raise ValueError("Refusing to publish without a product image URL")
        if not ad_notice.strip():
            raise ValueError("A post disclosure/notice is required before publication")

        caption = format_caption(deal, decision, ad_notice)
        button_text = "✈️ Проверить билет" if deal.offer_type == "flight" else ("🛍 Открыть предложение" if allow_direct_link else "🔥 Забрать скидку")

        # Download the image in the Actions runner and upload its bytes. This avoids
        # Telegram's remote-image fetch failing on hotlink protection or CDN rules.
        photo_bytes, mime_type, filename = _download_photo(
            deal.image_url,
            referer=deal.product_url,
            timeout=self.timeout,
        )
        fields = {
            "chat_id": self.chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(
                {"inline_keyboard": [[{"text": button_text, "url": target_url}]]},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        body, content_type = _multipart_form(
            fields,
            file_field="photo",
            filename=filename,
            mime_type=mime_type,
            file_bytes=photo_bytes,
        )
        endpoint = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": content_type, "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {response_body}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not publish to Telegram: network error ({type(exc.reason).__name__})") from None
        except TimeoutError:
            raise RuntimeError("Telegram publish request timed out") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError("Telegram API returned invalid JSON") from None

        if not isinstance(result, dict) or not result.get("ok"):
            description = str(result.get("description") or "unknown Telegram API error") if isinstance(result, dict) else "unexpected Telegram API response"
            raise RuntimeError(f"Telegram API error: {description}")
        message = result.get("result") or {}
        return message.get("message_id")
