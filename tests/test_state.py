from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from deal_hunter.models import Deal
from deal_hunter.state import count_published_within_hours, load_state, mark_published, save_state, should_skip_as_duplicate


class StateTests(unittest.TestCase):
    def make_deal(self, price=500):
        return Deal(
            title="Тестовый товар",
            product_url="https://shop.example.test/item/1",
            store="Тест",
            price=price,
            old_price=1000,
            affiliate_url="https://affiliate.example.test/item/1",
            image_url="https://cdn.example.test/item/1.jpg",
        )

    def test_save_load_and_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = load_state(path)
            mark_published(
                self.make_deal(),
                state,
                message_id=42,
                now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            )
            save_state(path, state)
            loaded = load_state(path)
            self.assertTrue(should_skip_as_duplicate(
                self.make_deal(),
                loaded,
                now=datetime(2026, 1, 1, 13, tzinfo=timezone.utc),
            ))

    def test_allows_repost_after_meaningful_price_drop(self):
        state = {"version": 1, "published": {}}
        mark_published(
            self.make_deal(price=500),
            state,
            message_id=42,
            now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        self.assertFalse(should_skip_as_duplicate(
            self.make_deal(price=400),
            state,
            repost_if_price_drop_percent=10,
            repost_min_wait_hours=6,
            now=datetime(2026, 1, 1, 20, tzinfo=timezone.utc),
        ))


    def test_allows_small_price_improvement_after_duplicate_cooldown(self):
        state = {"version": 1, "published": {}}
        mark_published(
            self.make_deal(price=500),
            state,
            message_id=42,
            now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        self.assertFalse(should_skip_as_duplicate(
            self.make_deal(price=480),
            state,
            cooldown_hours=72,
            repost_if_price_drop_percent=10,
            repost_min_wait_hours=6,
            now=datetime(2026, 1, 4, 13, tzinfo=timezone.utc),
        ))

    def test_counts_actual_posts_in_rolling_window(self):
        state = {"version": 1, "published": {}}
        mark_published(
            self.make_deal(price=500),
            state,
            message_id=42,
            now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            count_published_within_hours(
                state, hours=24,
                now=datetime(2026, 1, 1, 13, tzinfo=timezone.utc),
            ),
            1,
        )
        self.assertEqual(
            count_published_within_hours(
                state, hours=24,
                now=datetime(2026, 1, 2, 13, tzinfo=timezone.utc),
            ),
            0,
        )

if __name__ == "__main__":
    unittest.main()
