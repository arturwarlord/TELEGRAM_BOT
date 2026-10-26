from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


def telegram_api_call(
    token: str,
    method: str,
    params: dict[str, str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Call Telegram Bot API without ever including the token URL in errors."""
    if not token.strip():
        raise ValueError("TELEGRAM_BOT_TOKEN is empty")
    if not method.replace("_", "").isalnum():
        raise ValueError("Invalid Telegram API method name")

    query = urllib.parse.urlencode(params or {})
    url = f"https://api.telegram.org/bot{token}/{method}"
    if query:
        url += "?" + query
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "DealHunterBot/0.1"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            response_data = json.loads(exc.read().decode("utf-8", errors="replace"))
            description = str(response_data.get("description") or "request rejected")
        except (ValueError, AttributeError):
            description = "request rejected"
        # Never print str(exc): it can include the request URL and bot token.
        if exc.code == 401:
            raise RuntimeError("Telegram rejected the bot token (HTTP 401). Recheck TELEGRAM_BOT_TOKEN.") from None
        raise RuntimeError(f"Telegram API {method} returned HTTP {exc.code}: {description}") from None
    except urllib.error.URLError as exc:
        # The reason can safely be shown; avoid including the Request object/URL.
        raise RuntimeError(f"Network error while calling Telegram API {method}: {exc.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"Telegram API {method} timed out") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError(f"Telegram API {method} returned invalid JSON") from None

    if not isinstance(data, dict):
        raise RuntimeError(f"Telegram API {method} returned an unexpected response")
    if not data.get("ok"):
        description = str(data.get("description") or "unknown API error")
        raise RuntimeError(f"Telegram API {method} failed: {description}")
    result = data.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Telegram API {method} returned no result object")
    return result


def verify_telegram_setup(
    token: str,
    chat_id: str,
    call_api: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only verification; it does not send messages or create posts."""
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token:
        raise ValueError("Missing GitHub Secret TELEGRAM_BOT_TOKEN")
    if not chat_id:
        raise ValueError("Missing GitHub Actions Variable TELEGRAM_CHAT_ID")

    api = call_api or telegram_api_call
    bot = api(token, "getMe")
    bot_id = bot.get("id")
    if not bot_id:
        raise RuntimeError("Telegram getMe returned no bot ID")

    chat = api(token, "getChat", {"chat_id": chat_id})
    if str(chat.get("type", "")).lower() != "channel":
        raise RuntimeError(
            f"TELEGRAM_CHAT_ID resolves to chat type '{chat.get('type', 'unknown')}', not a channel. "
            "Use the public channel @username or the private channel's numeric ID."
        )

    try:
        member = api(token, "getChatMember", {"chat_id": chat_id, "user_id": str(bot_id)})
    except RuntimeError as exc:
        if "member list is inaccessible" in str(exc).lower():
            bot_username = str(bot.get("username") or "unknown")
            chat_title = str(chat.get("title") or chat_id)
            raise RuntimeError(
                f"Telegram could not verify @{bot_username} as a member of channel '{chat_title}'. "
                "Telegram reports 'member list is inaccessible'. This usually means the bot is not an "
                "administrator of this exact channel, or the admin change has not taken effect. "
                f"Open the channel '{chat_title}' in Telegram > Manage Channel > Administrators, add "
                f"@{bot_username} as an administrator, enable 'Post Messages', and save. Then confirm "
                "TELEGRAM_CHAT_ID points to this same channel and run Telegram Setup Check again."
            ) from None
        raise
    status = str(member.get("status", "")).lower()
    if status not in {"administrator", "creator"}:
        raise RuntimeError(
            f"Bot @{bot.get('username', 'unknown')} is not a channel administrator "
            f"(status={status or 'unknown'}). Add it as an admin with posting permission."
        )
    if status == "administrator" and member.get("can_post_messages") is not True:
        raise RuntimeError(
            f"Bot @{bot.get('username', 'unknown')} is an administrator but cannot post messages. "
            "Enable the channel administrator permission 'Post Messages'."
        )

    return {
        "bot_id": int(bot_id),
        "bot_username": str(bot.get("username") or ""),
        "chat_id": str(chat.get("id") or chat_id),
        "chat_title": str(chat.get("title") or ""),
        "chat_type": str(chat.get("type") or ""),
        "bot_status": status,
        "can_post_messages": True,
    }
