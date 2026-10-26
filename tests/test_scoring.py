from __future__ import annotations

import unittest
from datetime import datetime, timezone

from deal_hunter.models import Deal, canonical_url, parse_price
from deal_hunter.scoring import evaluate_deal


class DealScoringTests(unittest.TestCase):
    def make_deal(self, **changes):
        values = {
            "title": "Тестовый товар",
            "product_url": "https://shop.example.test/item/1",
            "store": "Тест",
            "price": 500,
            "old_price": 1000,
            "affiliate_url": "https://affiliate.example.test/click/1",
            "image_url": "https://cdn.example.test/item.jpg",
            "currency": "RUB",
        }
        values.update(changes)
        return Deal(**values)

    def test_accepts_clear_discount_with_link_and_image(self):
        decision = evaluate_deal(self.make_deal())
        self.assertTrue(decision.eligible)
        self.assertAlmostEqual(decision.discount_percent, 50.0)
        self.assertAlmostEqual(decision.savings, 500.0)

    def test_rejects_missing_old_price(self):
        decision = evaluate_deal(self.make_deal(old_price=None))
        self.assertFalse(decision.eligible)
        self.assertIn("missing current or old price", decision.reason)

    def test_rejects_too_small_discount(self):
        decision = evaluate_deal(self.make_deal(price=800, old_price=1000))
        self.assertFalse(decision.eligible)
        self.assertIn("below threshold", decision.reason)

    def test_rejects_suspicious_discount(self):
        decision = evaluate_deal(self.make_deal(price=50, old_price=1000))
        self.assertFalse(decision.eligible)
        self.assertIn("suspicious", decision.reason)

    def test_publish_requires_partner_url_and_image(self):
        no_link = evaluate_deal(self.make_deal(affiliate_url=None), require_affiliate=True)
        no_image = evaluate_deal(self.make_deal(image_url=None), require_image=True)
        self.assertIn("affiliate URL", no_link.reason)
        self.assertIn("image URL", no_image.reason)

    def test_expired_offer_is_rejected(self):
        decision = evaluate_deal(
            self.make_deal(expires_at="2020-01-01"),
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "offer expired")

    def test_price_parser_and_tracking_url_normalization(self):
        self.assertEqual(parse_price("1 299,00 RUB"), 1299.0)
        self.assertEqual(
            canonical_url("https://shop.example.test/item/1?utm_source=x&ref=abc&sku=42"),
            "https://shop.example.test/item/1?sku=42",
        )



    def test_accepts_explicit_coupon_without_fabricating_prices(self):
        deal = self.make_deal(
            price=None,
            old_price=None,
            affiliate_url=None,
            promo_code="SAVE25",
            reported_discount_percent=25,
            discount_is_maximum=False,
        )
        decision = evaluate_deal(
            deal,
            min_coupon_discount_percent=10,
            require_affiliate=False,
            require_image=True,
        )
        self.assertTrue(decision.eligible)
        self.assertAlmostEqual(decision.discount_percent, 25.0)
        self.assertIsNone(decision.savings)

    def test_accepts_maximum_coupon_discount_as_maximum_not_exact_price_claim(self):
        deal = self.make_deal(
            price=None,
            old_price=None,
            affiliate_url=None,
            promo_code="SAVE25",
            reported_discount_percent=25,
            discount_is_maximum=True,
        )
        decision = evaluate_deal(deal, require_affiliate=False)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.discount_percent, 25)
        self.assertIsNone(decision.savings)

    def test_rejects_coupon_without_explicit_code_or_percent(self):
        deal = self.make_deal(
            price=None,
            old_price=None,
            affiliate_url=None,
            promo_code=None,
            reported_discount_percent=None,
        )
        decision = evaluate_deal(deal, require_affiliate=False)
        self.assertFalse(decision.eligible)
        self.assertIn("missing price pair or explicit coupon", decision.reason)

    def test_rejects_suspicious_coupon_percentage(self):
        deal = self.make_deal(
            price=None,
            old_price=None,
            affiliate_url=None,
            promo_code="FREE100",
            reported_discount_percent=100,
        )
        decision = evaluate_deal(deal, require_affiliate=False)
        self.assertFalse(decision.eligible)
        self.assertIn("suspicious coupon discount", decision.reason)

    def test_rejects_expired_coupon(self):
        deal = self.make_deal(
            price=None,
            old_price=None,
            affiliate_url=None,
            promo_code="SAVE25",
            reported_discount_percent=25,
            expires_at="2020-01-01",
        )
        decision = evaluate_deal(
            deal,
            require_affiliate=False,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "offer expired")

if __name__ == "__main__":
    unittest.main()
