from __future__ import annotations

import unittest

from deal_hunter.telegram_check import verify_telegram_setup


class TelegramSetupCheckTests(unittest.TestCase):
    def test_accepts_admin_with_post_permission(self):
        responses = {
            "getMe": {"id": 12345, "username": "sample_deals_bot"},
            "getChat": {"id": -100123, "title": "Deals Channel", "type": "channel"},
            "getChatMember": {"status": "administrator", "can_post_messages": True},
        }
        calls = []

        def fake_api(token, method, params=None):
            calls.append((method, params))
            return responses[method]

        result = verify_telegram_setup("secret-token", "@deals", fake_api)
        self.assertEqual(result["bot_username"], "sample_deals_bot")
        self.assertEqual(result["chat_title"], "Deals Channel")
        self.assertTrue(result["can_post_messages"])
        self.assertEqual([call[0] for call in calls], ["getMe", "getChat", "getChatMember"])

    def test_rejects_missing_token(self):
        with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
            verify_telegram_setup("", "@deals", lambda *args: {})

    def test_rejects_missing_chat_id(self):
        with self.assertRaisesRegex(ValueError, "TELEGRAM_CHAT_ID"):
            verify_telegram_setup("secret-token", "", lambda *args: {})

    def test_rejects_group_instead_of_channel(self):
        def fake_api(token, method, params=None):
            if method == "getMe":
                return {"id": 12345, "username": "sample_deals_bot"}
            return {"id": -10, "title": "Some group", "type": "supergroup"}

        with self.assertRaisesRegex(RuntimeError, "not a channel"):
            verify_telegram_setup("secret-token", "@not_a_channel", fake_api)

    def test_rejects_bot_that_is_not_an_admin(self):
        def fake_api(token, method, params=None):
            if method == "getMe":
                return {"id": 12345, "username": "sample_deals_bot"}
            if method == "getChat":
                return {"id": -100123, "title": "Deals Channel", "type": "channel"}
            return {"status": "member"}

        with self.assertRaisesRegex(RuntimeError, "not a channel administrator"):
            verify_telegram_setup("secret-token", "@deals", fake_api)

    def test_rejects_admin_without_post_permission(self):
        def fake_api(token, method, params=None):
            if method == "getMe":
                return {"id": 12345, "username": "sample_deals_bot"}
            if method == "getChat":
                return {"id": -100123, "title": "Deals Channel", "type": "channel"}
            return {"status": "administrator", "can_post_messages": False}

        with self.assertRaisesRegex(RuntimeError, "cannot post messages"):
            verify_telegram_setup("secret-token", "@deals", fake_api)


if __name__ == "__main__":
    unittest.main()
