from __future__ import annotations

import json
import unittest
from pathlib import Path

from deal_hunter.sources import parse_feed


FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


class FeedParserTests(unittest.TestCase):
    def test_parses_xml_affiliate_feed_with_photo_and_prices(self):
        source = {
            "name": "test-affiliate-feed",
            "type": "xml",
            "enabled": True,
            "links_are_affiliate": False,
        }
        payload = FIXTURE.read_bytes()
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertIn("Наушники Example", deal.title)
        self.assertEqual(deal.price, 1000.0)
        self.assertEqual(deal.old_price, 2000.0)
        self.assertEqual(deal.store, "Тестовый магазин")
        self.assertEqual(deal.affiliate_url, "https://affiliate.example.test/click?id=abc")
        self.assertEqual(deal.image_url, "https://cdn.example.test/item-123.jpg")
        self.assertEqual(deal.discount_percent, 50.0)

    def test_reads_json_feed_with_custom_fields(self):
        payload = json.dumps({
            "offers": [{
                "name": "Чайник",
                "product_link": "https://shop.example.test/kettle",
                "tracking_link": "https://affiliate.example.test/kettle",
                "picture": "https://cdn.example.test/kettle.jpg",
                "sale_price": "1 000 RUB",
                "regular_price": "2 000 RUB",
                "merchant": "Магазин",
            }]
        }, ensure_ascii=False).encode()
        source = {
            "name": "json-test",
            "type": "json",
            "items_path": "offers",
            "fields": {
                "title": "name",
                "product_url": "product_link",
                "affiliate_url": "tracking_link",
                "image_url": "picture",
                "price": "sale_price",
                "old_price": "regular_price",
                "store": "merchant",
            },
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].price, 1000.0)
        self.assertEqual(deals[0].old_price, 2000.0)
        self.assertEqual(deals[0].affiliate_url, "https://affiliate.example.test/kettle")

    def test_uses_product_url_only_when_feed_is_declared_affiliate(self):
        payload = b'{"items":[{"title":"T","url":"https://shop.example.test/1","price":100,"old_price":200}]}'
        source = {"name": "declared-affiliate", "type": "json", "links_are_affiliate": True}
        deals = parse_feed(payload, source)
        self.assertEqual(deals[0].affiliate_url, deals[0].product_url)

    def test_rejects_records_without_product_url(self):
        payload = b'{"items":[{"title":"No URL","price":100,"old_price":200}]}'
        deals = parse_feed(payload, {"name": "json-test", "type": "json"})
        self.assertEqual(deals, [])



    def test_parses_public_html_retailer_card_with_old_and_current_prices(self):
        payload = """
        <html><body><main>
          <div class="product-card">
            <a href="/product/headphones"><img src="/images/headphones.jpg" alt="Наушники Example"></a>
            <a href="/product/headphones">Наушники Example</a>
            <span>Цена 1 000 ₽</span>
            <span>Старая цена 2 000 ₽</span>
          </div>
        </main></body></html>
        """.encode("utf-8")
        source = {
            "name": "test-retailer",
            "type": "html",
            "enabled": True,
            "url": "https://shop.example.test/promotions",
            "store": "Тест-магазин",
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].title, "Наушники Example")
        self.assertEqual(deals[0].product_url, "https://shop.example.test/product/headphones")
        self.assertEqual(deals[0].image_url, "https://shop.example.test/images/headphones.jpg")
        self.assertEqual(deals[0].price, 1000.0)
        self.assertEqual(deals[0].old_price, 2000.0)
        self.assertIsNone(deals[0].affiliate_url)

    def test_parses_kuper_style_discounted_price_labels(self):
        payload = """
        <div class="product-card">
          <a href="/product/pasta"><img src="https://cdn.example.test/pasta.jpg" alt="Макароны Example 400 г"></a>
          <a href="/product/pasta">Макароны Example 400 г</a>
          <span>Скидка-38%</span>
          <span>Цена без скидки за 1 шт. 99,99 ₽</span>
          <span>Цена со скидкой за 1 шт. 61,99 ₽</span>
        </div>
        """.encode()
        source = {
            "name": "test-kuper",
            "type": "html",
            "enabled": True,
            "url": "https://kuper.ru/shop/c/sale",
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].price, 61.99)
        self.assertEqual(deals[0].old_price, 99.99)

    def test_html_parser_does_not_invent_old_price(self):
        payload = """
        <div class="product-card">
          <a href="/product/item"><img src="/item.jpg" alt="Просто товар"></a>
          <a href="/product/item">Просто товар</a>
          <span>Цена 1 000 ₽</span>
        </div>
        """.encode("utf-8")
        source = {
            "name": "test-retailer",
            "type": "html",
            "enabled": True,
            "url": "https://shop.example.test/promotions",
        }
        self.assertEqual(parse_feed(payload, source), [])


    def test_parses_public_html_sale_card_with_parenthesized_discount(self):
        payload = """
        <html><body>
          <article class="deal">
            <a href="/deals/coffee-123">Кофе зерновой Example 1 кг</a>
            <img src="/images/coffee.jpg" alt="Кофе зерновой Example">
            <div>799 ₽</div><del>1 299 ₽</del><span>(38%)</span>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "Pepper public discounts",
            "type": "html",
            "url": "https://www.pepper.ru/",
            "store": "Pepper.ru",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertEqual(deal.title, "Кофе зерновой Example 1 кг")
        self.assertEqual(deal.price, 799.0)
        self.assertEqual(deal.old_price, 1299.0)
        self.assertEqual(deal.discount_percent, (1299.0 - 799.0) / 1299.0 * 100)
        self.assertEqual(deal.product_url, "https://www.pepper.ru/deals/coffee-123")
        self.assertEqual(deal.image_url, "https://www.pepper.ru/images/coffee.jpg")
        self.assertIsNone(deal.affiliate_url)

    def test_html_parser_does_not_invent_a_discount_without_price_pair(self):
        payload = """
        <html><body>
          <article>
            <a href="/deals/random">Случайный товар за выгодной ценой</a>
            <img src="/img/item.jpg">
            <div>999 ₽</div><span>Популярный товар сегодня</span>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-html",
            "type": "html",
            "url": "https://shop.example.test/sale",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])


    def test_parses_html_coupon_card_without_inventing_prices(self):
        from deal_hunter.sources import parse_feed

        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/offers/summer-jacket">Куртка со скидкой</a>
            <img src="/images/jacket.jpg" alt="Куртка">
            <div>Скидка до 25%</div>
            <span>Промокод: SAVE25</span>
            <span>до 31.12.2026</span>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-coupons",
            "type": "html_promos",
            "url": "https://promos.example.test/catalog",
            "store": "Тест промокодов",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertEqual(deal.title, "Куртка со скидкой")
        self.assertEqual(deal.product_url, "https://promos.example.test/offers/summer-jacket")
        self.assertEqual(deal.image_url, "https://promos.example.test/images/jacket.jpg")
        self.assertEqual(deal.promo_code, "SAVE25")
        self.assertEqual(deal.reported_discount_percent, 25)
        self.assertTrue(deal.discount_is_maximum)
        self.assertIsNone(deal.price)
        self.assertIsNone(deal.old_price)
        self.assertEqual(deal.expires_at, "2026-12-31")

    def test_html_promotions_ignores_navigation_without_explicit_offer(self):
        payload = """
        <html><body>
          <nav><a href="/catalog">Каталог</a><span>Скидки и акции</span></nav>
          <article>
            <a href="/offers/jacket">Куртка обычная</a>
            <img src="/images/jacket.jpg">
            <span>Новая коллекция</span>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-coupons",
            "type": "html_promos",
            "url": "https://promos.example.test/",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])


    def test_html_promotions_never_use_outbound_tracking_urls_or_category_links(self):
        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/categories/products">Скидки на товары до 50%</a>
            <a href="https://retailer.example.test/click?erid=example">Перейти на сайт</a>
            <img src="/images/store.jpg" alt="Магазин">
            <div>Скидка до 50%</div>
            <span>Промокод: SAVE50</span>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-coupons",
            "type": "html_promos",
            "url": "https://promos.example.test/catalog",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])


    def test_html_promotions_normalizes_russian_month_expiry(self):
        from datetime import datetime, timezone

        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/promo/october">Скидка 25% на заказ</a>
            <img src="/images/deal.jpg" alt="Товар">
            <span>Промокод: OCT25</span>
            <span>до 31 октября</span>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-coupons",
            "type": "html_promos",
            "url": "https://promos.example.test/catalog",
            "enabled": True,
        }
        deal = parse_feed(payload, source)[0]
        current_year = datetime.now(timezone.utc).year
        self.assertEqual(deal.expires_at, f"{current_year}-10-31")

    def test_html_promo_description_excludes_tracking_and_erid_footer(self):
        payload = """
        <html><body>
          <article>
            <a href="/promo/netprint">Скидка 40% на фотокниги</a>
            <img src="/images/netprint.jpg">
            <span>Промокод: ALL40</span>
            <p>Скидка на фотопечать и фотокниги по промокоду.</p>
            <a href="https://retailer.example.test/click?erid=abc">Перейти на сайт</a>
            <p>Реклама. ООО «Пример». ИНН 1234567890. erid: 2RanyExample</p>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-coupons",
            "type": "html_promos",
            "url": "https://promos.example.test/catalog",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertIn("Скидка на фотопечать", deals[0].description)
        self.assertNotIn("erid", deals[0].description.lower())
        self.assertNotIn("реклама.", deals[0].description.lower())


    def test_does_not_attach_unrelated_percent_to_free_points_offer(self):
        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/promo/points">Как получить бесплатно 1000 ВАУ баллов Озона</a>
            <img src="/images/points.jpg" alt="Бонусы">
            <p>Условия акции для новых пользователей.</p>
            <div>Перейти на сайт</div>
            <aside>Скидки до 80% на другие товары</aside>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-coupons",
            "type": "html_promos",
            "url": "https://promos.example.test/catalog",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])

if __name__ == "__main__":
    unittest.main()
