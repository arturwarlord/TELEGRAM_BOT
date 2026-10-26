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
        # A large price drop can be reposted sooner; a small improvement waits for
        # the general duplicate cooldown. An unchanged or higher price stays skipped.
        if drop_percent >= repost_if_price_drop_percent and elapsed_hours >= repost_min_wait_hours:
            return False
        if elapsed_hours >= cooldown_hours:
            return False

    return True


def count_published_within_hours(
    state: dict[str, Any],
    *,
    hours: float = 24.0,
    now: datetime | None = None,
) -> int:
    """Count actual posts, including price-drop reposts, within the rolling window."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    cutoff = current_time.astimezone(timezone.utc).timestamp() - max(0.0, hours) * 3600.0
    recent_posts = state.get("recent_posts")
    if isinstance(recent_posts, list):
        count = 0
        for entry in recent_posts:
            if not isinstance(entry, dict):
                continue
            timestamp = _parse_timestamp(entry.get("published_at"))
            if timestamp and timestamp.timestamp() >= cutoff:
                count += 1
        return count

    # Backward compatibility for a state file created before recent_posts existed.
    published = state.get("published", {})
    if not isinstance(published, dict):
        return 0
    count = 0
    for entry in published.values():
        if not isinstance(entry, dict):
            continue
        timestamp = _parse_timestamp(entry.get("published_at"))
        if timestamp and timestamp.timestamp() >= cutoff:
            count += 1
    return count


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
    timestamp = current_time.astimezone(timezone.utc).isoformat()
    state.setdefault("published", {})[deal.key] = {
        "title": deal.title[:240],
        "store": deal.store[:100],
        "price": deal.price,
        "published_at": timestamp,
        "telegram_message_id": message_id,
        "affiliate_url": deal.affiliate_url,
    }
    recent_posts = state.setdefault("recent_posts", [])
    if isinstance(recent_posts, list):
        recent_posts.append({
            "key": deal.key,
            "published_at": timestamp,
            "telegram_message_id": message_id,
        })
        # Bound file growth while keeping enough history for daily rate limits.
        cutoff = current_time.astimezone(timezone.utc).timestamp() - 7 * 24 * 3600
        state["recent_posts"] = [
            entry for entry in recent_posts
            if isinstance(entry, dict)
            and (parsed := _parse_timestamp(entry.get("published_at"))) is not None
            and parsed.timestamp() >= cutoff
        ]
