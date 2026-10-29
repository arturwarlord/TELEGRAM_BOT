from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from deal_hunter.scoring import evaluate_deal
from deal_hunter.takprodam import (
    fetch_commissions,
    fetch_configured_takprodam_deals,
    fetch_promotion_deals,
    parse_promotion_items,
    resolve_source_id,
    summarize_commissions,
)


class TakprodamTests(unittest.TestCase):
    def sample_item(self):
        return {
            "id": "deal-1",
            "product_id": "sku-123",
            "title": "Умные часы Example",
            "image_url": "https://cdn.example.test/watch.jpg",
            "price": 12000,
            "price_discount": 6000,
            "discount_percent": 50,
            "commission": 120.0,
            "marketplace_title": "Ozon",
            "store_title": "Продавец Example",
            "external_link": "https://www.ozon.ru/product/example-123/",
            "tracking_link": "https://track.example.test/abc123",
            "legal_text": "Реклама. Данные рекламодателя из партнёрского API.",
        }

    @patch("deal_hunter.takprodam.fetch_promotion_deals", return_value=[])
    @patch("deal_hunter.takprodam.resolve_source_id", return_value="24285")
    def test_blank_optional_api_limits_use_safe_defaults(self, mock_resolve, mock_fetch):
        environment = {
            "TAKPRODAM_API_TOKEN": "dummy-token",
            "TAKPRODAM_SOURCE_ID": "",
            "TAKPRODAM_MARKETPLACES": "Ozon,Wildberries",
            "TAKPRODAM_LIMIT": "",
            "TAKPRODAM_MAX_PAGES": "",
            "TAKPRODAM_SUBID": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            deals, source_id = fetch_configured_takprodam_deals()
        self.assertEqual(deals, [])
        self.assertEqual(source_id, "24285")
        self.assertEqual(mock_fetch.call_args.kwargs["limit"], 1000)
        self.assertEqual(mock_fetch.call_args.kwargs["max_pages"], 1)
        mock_resolve.assert_called_once()

    @patch("deal_hunter.takprodam.fetch_promotion_deals", return_value=[])
    @patch("deal_hunter.takprodam.resolve_source_id", return_value="24285")
    def test_malformed_optional_api_limits_use_safe_defaults(self, mock_resolve, mock_fetch):
        environment = {
            "TAKPRODAM_API_TOKEN": "dummy-token",
            "TAKPRODAM_SOURCE_ID": "",
            "TAKPRODAM_MARKETPLACES": "Ozon",
            "TAKPRODAM_LIMIT": "invalid",
            "TAKPRODAM_MAX_PAGES": "-2",
            "TAKPRODAM_SUBID": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            fetch_configured_takprodam_deals()
        self.assertEqual(mock_fetch.call_args.kwargs["limit"], 1000)
        self.assertEqual(mock_fetch.call_args.kwargs["max_pages"], 1)

    def test_maps_promotion_product_into_deal(self):
        deals = parse_promotion_items([self.sample_item()], "Ozon")
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertEqual(deal.title, "Умные часы Example")
        self.assertEqual(deal.price, 6000)
        self.assertEqual(deal.old_price, 12000)
        self.assertEqual(deal.discount_percent, 50)
        self.assertEqual(deal.affiliate_url, "https://track.example.test/abc123")
        self.assertEqual(deal.product_url, "https://www.ozon.ru/product/example-123/")
        self.assertEqual(deal.image_url, "https://cdn.example.test/watch.jpg")
        self.assertEqual(deal.commission, 120.0)
        self.assertIn("Реклама", deal.legal_text)

    def test_parser_diagnostics_explain_rejected_promotion_products(self):
        valid = self.sample_item()
        missing_prices = dict(valid)
        missing_prices.pop("price_discount")
        missing_prices.pop("discount_percent")
        other_market = dict(valid)
        other_market["marketplace_title"] = "Wildberries"
        missing_link = dict(valid)
        missing_link["tracking_link"] = ""
        diagnostics = {}
        deals = parse_promotion_items(
            [valid, missing_prices, other_market, missing_link],
            "Ozon",
            diagnostics=diagnostics,
        )
        self.assertEqual(len(deals), 1)
        self.assertEqual(diagnostics, {
            "received": 4,
            "accepted": 1,
            "rejected_missing_or_invalid_discount_price_pair": 1,
            "rejected_marketplace_mismatch": 1,
            "rejected_missing_title_or_tracking_link": 1,
        })

    def test_accepts_explicit_discount_percent_when_price_pair_is_incomplete(self):
        item = self.sample_item()
        item.pop("price")
        deals = parse_promotion_items([item], "Ozon")
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].price, 6000)
        self.assertIsNone(deals[0].old_price)
        self.assertEqual(deals[0].reported_discount_percent, 50)
        decision = evaluate_deal(deals[0], require_affiliate=True, require_image=True)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.discount_percent, 50)

    def test_does_not_accept_incomplete_price_pair_without_explicit_discount(self):
        item = self.sample_item()
        item.pop("price_discount")
        item.pop("discount_percent")
        self.assertEqual(parse_promotion_items([item], "Ozon"), [])

    def test_keeps_explicit_discount_when_price_pair_conflicts_without_showing_prices(self):
        item = self.sample_item()
        item["price_discount"] = 13000
        item["discount_percent"] = 50
        deals = parse_promotion_items([item], "Ozon")
        self.assertEqual(len(deals), 1)
        self.assertIsNone(deals[0].price)
        self.assertIsNone(deals[0].old_price)
        self.assertEqual(deals[0].reported_discount_percent, 50)

    def test_reverses_price_pair_only_when_explicit_discount_confirms_it(self):
        item = self.sample_item()
        item["price"] = 6000
        item["price_discount"] = 12000
        item["discount_percent"] = 50
        deals = parse_promotion_items([item], "Ozon")
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].price, 6000)
        self.assertEqual(deals[0].old_price, 12000)
        self.assertIsNone(deals[0].reported_discount_percent)

    def test_rejects_unavailable_or_invalid_price_pair(self):
        invalid = self.sample_item()
        invalid["price_discount"] = 13000
        deals = parse_promotion_items([invalid], "Ozon")
        self.assertEqual(deals, [])

    def test_filters_marketplace(self):
        deals = parse_promotion_items([self.sample_item()], "Wildberries")
        self.assertEqual(deals, [])

    def test_mapped_deal_is_ready_for_normal_quality_filter(self):
        deals = parse_promotion_items([self.sample_item()], "Ozon")
        decision = evaluate_deal(deals[0], require_affiliate=True, require_image=True)
        self.assertTrue(decision.eligible)
        self.assertAlmostEqual(decision.discount_percent, 50)

    @patch("deal_hunter.takprodam._request_json")
    def test_resolves_single_approved_source(self, mock_request):
        mock_request.return_value = {
            "items": [
                {"id": 7, "title": "Telegram deals", "status": "approved"},
                {"id": 8, "title": "Pending website", "status": "verification"},
            ]
        }
        self.assertEqual(resolve_source_id("dummy-token"), "7")
        mock_request.assert_called_once()

    @patch("deal_hunter.takprodam._request_json")
    def test_configured_source_must_be_approved(self, mock_request):
        mock_request.return_value = {
            "items": [
                {"id": 7, "title": "Telegram deals", "status": "approved"},
                {"id": 8, "title": "Pending website", "status": "verification", "created_at": "2026-10-20T12:00:00Z"},
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "not approved yet.*created_at=2026-10-20T12:00:00Z.*48 hours"):
            resolve_source_id("dummy-token", "8")


    def test_same_product_dedupes_across_promotion_links(self):
        first = self.sample_item()
        second = dict(first)
        second["tracking_link"] = "https://track.example.test/other-promotion"
        deals = parse_promotion_items([first, second], "Ozon")
        self.assertEqual(len(deals), 2)
        self.assertEqual(deals[0].product_url, "https://www.ozon.ru/product/example-123/")
        self.assertEqual(deals[0].key, deals[1].key)

    def test_unwraps_takprodam_redirect_to_clean_direct_marketplace_url(self):
        item = self.sample_item()
        item["external_link"] = (
            "https://takprdm.ru/offer?id=secret&redirectTo="
            "https%3A%2F%2Fwww.ozon.ru%2Fproduct%2Fexample-123%2F%3Futm_source%3Dbot"
            "&erid=campaign-id&subid=telegram_deal_hunter"
        )
        deal = parse_promotion_items([item], "Ozon")[0]
        self.assertEqual(
            deal.product_url,
            "https://www.ozon.ru/product/example-123/",
        )

    def test_accepts_partner_tracking_url_when_direct_product_url_is_unavailable(self):
        item = self.sample_item()
        item.pop("external_link")
        item["tracking_link"] = "https://takprdm.ru/offer?id=tracking-only"
        deals = parse_promotion_items([item], "Ozon")
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].affiliate_url, "https://takprdm.ru/offer?id=tracking-only")
        # The tracking URL is only the internal fallback; publishing still uses
        # affiliate_url unless direct-link mode is explicitly selected.
        self.assertEqual(deals[0].product_url, "https://takprdm.ru/offer?id=tracking-only")

    @patch("deal_hunter.takprodam._request_json")
    def test_explains_when_channel_source_is_pending_approval(self, mock_request):
        mock_request.return_value = {
            "items": [
                {"id": 21, "title": "Telegram Channel", "status": "verification"}
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "Telegram Channel=verification"):
            resolve_source_id("dummy-token")


    @patch("deal_hunter.takprodam._request_json")
    def test_default_markets_include_supported_marketplaces(self, mock_request):
        mock_request.return_value = {"items": []}
        fetch_promotion_deals("dummy-token", "7", limit=20, max_pages=1)
        self.assertEqual(
            [call.kwargs["params"]["marketplace"] for call in mock_request.call_args_list],
            ["ozon", "wildberries", "avito", "aliexpress"],
        )

    @patch("deal_hunter.takprodam._request_json")
    def test_promotion_fetch_passes_subid_for_attribution(self, mock_request):
        mock_request.return_value = {"items": []}
        fetch_promotion_deals(
            "dummy-token",
            "7",
            ["Ozon"],
            limit=20,
            max_pages=1,
            subid="telegram_deal_hunter",
        )
        self.assertEqual(
            mock_request.call_args.kwargs["params"],
            {
                "source_id": "7",
                "marketplace": "ozon",
                "page": 1,
                "limit": 20,
                "subid": "telegram_deal_hunter",
            },
        )

    @patch("deal_hunter.takprodam._request_json")
    def test_fetch_commissions_filters_by_subid(self, mock_request):
        mock_request.return_value = {
            "total_count": 1,
            "items": [{
                "id": "commission-1",
                "status": "approved",
                "commission_amount": 125.5,
                "cart_amount": 2500,
                "subid": "telegram_deal_hunter",
            }],
        }
        items = fetch_commissions(
            "dummy-token",
            subid="telegram_deal_hunter",
            limit=50,
            max_pages=3,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(mock_request.call_args.kwargs["params"]["subid"], "telegram_deal_hunter")
        self.assertEqual(mock_request.call_args.kwargs["params"]["page"], 1)

    def test_summarizes_commissions_by_status_without_order_details(self):
        summary = summarize_commissions([
            {"status": "approved", "commission_amount": 10.25, "cart_amount": 100},
            {"status": "approved", "commission_amount": 1.75, "cart_amount": 50},
            {"status": "pending", "commission_amount": 5, "cart_amount": 25},
            {"status": "declined", "commission_amount": 3, "cart_amount": 30},
        ])
        self.assertEqual(summary["approved"], {
            "count": 2,
            "commission_amount": 12.0,
            "cart_amount": 150.0,
        })
        self.assertEqual(summary["pending"]["count"], 1)
        self.assertEqual(summary["declined"]["count"], 1)

    def test_rejects_unsupported_commission_status_filter(self):
        with self.assertRaisesRegex(ValueError, "Commission status must be"):
            fetch_commissions("dummy-token", status="confirmed")

if __name__ == "__main__":
    unittest.main()
