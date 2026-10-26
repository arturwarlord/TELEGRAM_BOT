from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Deal


def empty_state() -> dict[str, Any]:
    return {"version": 1, "published": {}}


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("published"), dict):
            data.setdefault("version", 1)
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return empty_state()


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def should_skip_as_duplicate(
    deal: Deal,
    state: dict[str, Any],
    *,
    cooldown_hours: float = 72.0,
    repost_if_price_drop_percent: float = 10.0,
    repost_min_wait_hours: float = 6.0,
    now: datetime | None = None,
) -> bool:
    """Skip prior products unless a meaningful price drop merits a new post."""
    published = state.setdefault("published", {})
    previous = published.get(deal.key)
    if not isinstance(previous, dict):
        return False

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    last_time = _parse_timestamp(previous.get("published_at"))
    elapsed_hours = 0.0 if last_time is None else (current_time.astimezone(timezone.utc) - last_time).total_seconds() / 3600.0

    old_post_price = previous.get("price")
    try:
        old_post_price = float(old_post_price)
    except (TypeError, ValueError):
        old_post_price = 0.0

    if old_post_price > 0 and deal.price is not None and deal.price < old_post_price:
        drop_percent = (old_post_price - deal.price) / old_post_price * 100.0
        if drop_percent >= repost_if_price_drop_percent and elapsed_hours >= repost_min_wait_hours:
            return False

    # Do not repost unchanged prices, even after the cooldown; the cooldown only
    # controls how quickly a qualifying lower price may be republished.
    return True


def mark_published(
    deal: Deal,
    state: dict[str, Any],
    *,
    message_id: int | None = None,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    state.setdefault("published", {})[deal.key] = {
        "title": deal.title[:240],
        "store": deal.store[:100],
        "price": deal.price,
        "published_at": current_time.astimezone(timezone.utc).isoformat(),
        "telegram_message_id": message_id,
        "affiliate_url": deal.affiliate_url,
    }
