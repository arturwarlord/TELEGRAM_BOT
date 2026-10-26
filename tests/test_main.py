from __future__ import annotations

import unittest

from deal_hunter.models import Deal
from main import choose_best_duplicates


class MainDeduplicationTests(unittest.TestCase):
    def make_coupon(self, **changes):
        values = {
            "title": "Пятёрочка Доставка октябрь 2026 — 55% на повторный заказ",
            "product_url": "https://berikod.ru/promo/pyaterochka-dostavka-skidka-55/",
            "store": "БериКод",
            "source": "test-coupons",
            "image_url": "https://cdn.example.test/deal.jpg",
            "reported_discount_percent": 55,
            "discount_is_maximum": False,
        }
        values.update(changes)
        return Deal(**values)

    def test_prefers_detailed_offer_page_over_same_merchant_store_page(self):
        generic = self.make_coupon(
            title="Пятёрочка Доставка",
            product_url="https://promofact.ru/store/pyaterochka",
            store="ПромоФакт",
        )
        detailed = self.make_coupon()
        result = choose_best_duplicates([generic, detailed])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].product_url, detailed.product_url)

    def test_code_card_beats_uncoded_duplicate_of_same_discount(self):
        generic = self.make_coupon(
            title="Купон Netprint",
            product_url="https://promofact.ru/store/netprint",
            reported_discount_percent=40,
        )
        coded = self.make_coupon(
            title="Купон Netprint",
            product_url="https://promofact.ru/store/netprint",
            promo_code="ALL40",
            reported_discount_percent=40,
        )
        result = choose_best_duplicates([generic, coded])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].promo_code, "ALL40")

    def test_keeps_two_different_codes_for_same_merchant_and_discount(self):
        first = self.make_coupon(promo_code="SAVE25", reported_discount_percent=25)
        second = self.make_coupon(promo_code="NEW25", reported_discount_percent=25)
        result = choose_best_duplicates([first, second])
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
