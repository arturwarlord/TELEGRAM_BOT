from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from deal_hunter.models import Deal
from deal_hunter.scoring import DealDecision
from deal_hunter.telegram import (
    TelegramPublisher,
    _detect_image_format,
    _download_photo,
)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class TelegramPublisherTests(unittest.TestCase):
    def make_deal(self, **changes):
        values = {
            "title": "Корм для животных со скидкой",
            "product_url": "https://offers.example.test/promo/food",
            "store": "Тестовый источник",
            "price": 700,
            "old_price": 1000,
            "affiliate_url": "https://affiliate.example.test/click/123",
            "image_url": "https://cdn.example.test/food.jpg",
            "currency": "RUB",
        }
        values.update(changes)
        return Deal(**values)

    def test_detects_supported_image_signatures(self):
        self.assertEqual(_detect_image_format(b"\xff\xd8\xffjpeg-data"), ("image/jpeg", "offer.jpg"))
        self.assertEqual(
            _detect_image_format(b"\x89PNG\r\n\x1a\nimage-data"),
            ("image/png", "offer.png"),
        )
        self.assertEqual(_detect_image_format(b"GIF89aimage-data"), ("image/gif", "offer.gif"))
        self.assertEqual(
            _detect_image_format(b"RIFF\x10\x00\x00\x00WEBPimage-data"),
            ("image/webp", "offer.webp"),
        )
        self.assertIsNone(_detect_image_format(b"<html>not an image</html>"))

    @patch("deal_hunter.telegram.urllib.request.urlopen")
    def test_download_rejects_html_disguised_as_an_image(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(b"<html>anti-bot page</html>")
        with self.assertRaisesRegex(RuntimeError, "did not return a supported"):
            _download_photo(
                "https://cdn.example.test/not-an-image",
                referer="https://offers.example.test/promo",
                timeout=2,
            )

    @patch("deal_hunter.telegram._download_photo")
    @patch("deal_hunter.telegram.urllib.request.urlopen")
    def test_direct_mode_uploads_image_bytes_and_not_affiliate_url(self, mock_urlopen, mock_download):
        mock_download.return_value = (b"\xff\xd8\xfffake-jpeg", "image/jpeg", "offer.jpg")
        mock_urlopen.return_value = FakeResponse(
            json.dumps({"ok": True, "result": {"message_id": 99}}).encode("utf-8")
        )
        deal = self.make_deal()
        decision = DealDecision(True, "eligible", 30.0, 300.0, 66.0)

        message_id = TelegramPublisher("dummy-token", "@test_channel").publish(
            deal,
            decision,
            "Публичная ссылка; канал не получает комиссию.",
            allow_direct_link=True,
        )

        self.assertEqual(message_id, 99)
        mock_download.assert_called_once_with(
            deal.image_url,
            referer=deal.product_url,
            timeout=25,
        )
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("multipart/form-data; boundary=", request.get_header("Content-type"))
        body = request.data
        self.assertIn(b'filename="offer.jpg"', body)
        self.assertIn(b'name="photo"', body)
        self.assertIn(b"https://offers.example.test/promo/food", body)
        self.assertNotIn(b"affiliate.example.test", body)
        self.assertIn("Публичная ссылка".encode("utf-8"), body)

    @patch("deal_hunter.telegram._download_photo")
    @patch("deal_hunter.telegram.urllib.request.urlopen")
    def test_partner_mode_uses_tracking_url_when_available(self, mock_urlopen, mock_download):
        mock_download.return_value = (b"\\xff\\xd8\\xfffake-jpeg", "image/jpeg", "offer.jpg")
        mock_urlopen.return_value = FakeResponse(
            json.dumps({"ok": True, "result": {"message_id": 100}}).encode("utf-8")
        )
        deal = self.make_deal()
        decision = DealDecision(True, "eligible", 30.0, 300.0, 66.0)

        TelegramPublisher("dummy-token", "@test_channel").publish(
            deal, decision, "Партнёрская ссылка: канал может получить вознаграждение."
        )

        request = mock_urlopen.call_args.args[0]
        self.assertIn(b"https://affiliate.example.test/click/123", request.data)
        self.assertNotIn(b"https://offers.example.test/promo/food", request.data)

    @patch("deal_hunter.telegram._download_photo")
    @patch("deal_hunter.telegram.urllib.request.urlopen")
    def test_direct_mode_does_not_call_telegram_if_image_download_fails(self, mock_urlopen, mock_download):
        mock_download.side_effect = RuntimeError("Could not download offer image (HTTP 403)")
        deal = self.make_deal()
        decision = DealDecision(True, "eligible", 30.0, 300.0, 66.0)

        with self.assertRaisesRegex(RuntimeError, "offer image"):
            TelegramPublisher("dummy-token", "@test_channel").publish(
                deal,
                decision,
                "Публичная ссылка; канал не получает комиссию.",
                allow_direct_link=True,
            )
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()

