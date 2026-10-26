from __future__ import annotations

import unittest

from deal_hunter.models import Deal
from deal_hunter.scoring import DealDecision
from deal_hunter.telegram import format_caption


class CouponCaptionTests(unittest.TestCase):
    def test_coupon_caption_does_not_claim_old_or_current_price(self):
        deal = Deal(
            title="Куртка со скидкой",
            product_url="https://shop.example.test/offers/jacket",
            store="ПромоФакт",
            source="test-coupons",
            image_url="https://cdn.example.test/jacket.jpg",
            promo_code="SAVE25",
            description="Скидка на выбранные товары при заказе от 3 000 ₽.",
            reported_discount_percent=25,
            discount_is_maximum=True,
        )
        decision = DealDecision(
            eligible=True,
            reason="eligible coupon",
            discount_percent=25,
            savings=None,
            score=30,
        )
        caption = format_caption(
            deal,
            decision,
            "Ссылка ведёт на публичную страницу предложения. Канал не получает комиссию с перехода.",
        )
        self.assertIn("скидка до 25%", caption)
        self.assertIn("SAVE25", caption)
        self.assertIn("ПромоФакт", caption)
        self.assertIn("не получает комиссию", caption)
        self.assertNotIn("Было:", caption)
        self.assertNotIn("Сейчас:", caption)
        self.assertNotIn("Экономия:", caption)


if __name__ == "__main__":
    unittest.main()
