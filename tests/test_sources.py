from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from deal_hunter.sources import _decode_html_payload, _extract_open_graph_image, _extract_promo_expiry, parse_aviasales_payload, parse_feed, parse_html_travel_articles, parse_html_tour_cards


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



    def test_html_promotions_removes_card_counters_and_cta_from_title(self):
        html = """
        <html><body><main>
          <article class="deal">
            <a href="/deals/quest">-80 % Получили: 2494 Получи первым! Квест в Москве Бесплатно Подробнее</a>
            <img src="/images/quest.jpg" alt="Квест в Москве">
          </article>
        </main></body></html>
        """
        deals = parse_feed(html.encode("utf-8"), {
            "name": "test-title-cleanup",
            "type": "html_promos",
            "url": "https://coupon.example.test/",
            "store": "Test Coupon",
        })
        matching = [deal for deal in deals if deal.product_url.endswith("/deals/quest")]
        self.assertTrue(matching)
        self.assertNotIn("2494", matching[0].title)
        self.assertNotIn("Подробнее", matching[0].title)
        self.assertNotIn("Получили", matching[0].title)
        self.assertIn("Квест в Москве", matching[0].title)
        self.assertNotIn("Бесплатно", matching[0].title)
        self.assertNotIn("Получить первым", matching[0].title)

    def test_html_promotions_recognizes_en_dash_discount_before_coupon_discount(self):
        html = """
        <html><body>
          <article class="deal">
            <a href="/deals/flowers">–30% -10% промокод «SALE10» — букет цветов</a>
            <img src="/images/flowers.jpg" alt="Букет цветов">
          </article>
        </body></html>
        """
        deals = parse_feed(html.encode("utf-8"), {
            "name": "test-unicode-discount-dash",
            "type": "html_promos",
            "url": "https://coupon.example.test/",
            "store": "Test Coupon",
        })
        matching = [deal for deal in deals if deal.product_url.endswith("/deals/flowers")]
        self.assertTrue(matching)
        self.assertEqual(matching[0].reported_discount_percent, 30)

    def test_html_promotions_extracts_explicit_current_and_old_prices(self):
        html = """
        <html><body>
          <article class="deal">
            <a href="/deals/quest-50">Скидка 50%. Квест «Припять» (3995 руб. вместо 7990 руб.)</a>
            <img src="/images/quest.jpg" alt="Квест">
          </article>
        </body></html>
        """
        deals = parse_feed(html.encode("utf-8"), {
            "name": "test-coupon-prices",
            "type": "html_promos",
            "url": "https://coupon.example.test/",
            "store": "Test Coupon",
        })
        matching = [deal for deal in deals if deal.product_url.endswith("/deals/quest-50")]
        self.assertTrue(matching)
        self.assertEqual(matching[0].price, 3995.0)
        self.assertEqual(matching[0].old_price, 7990.0)

    def test_html_promotions_support_windows_1251_pages(self):
        html = '''
        <html><head><meta charset="windows-1251"></head><body>
          <article>
            <h3><a href="/promo/hotel">Скидка 25% на отель</a></h3>
            <img src="/images/hotel.jpg" alt="Отель">
            <p>Скидка 25% на бронирование отеля.</p>
          </article>
        </body></html>
        '''
        payload = html.encode("cp1251")
        self.assertIn("Скидка 25%", _decode_html_payload(payload))
        deals = parse_feed(payload, {
            "name": "test-windows-1251",
            "type": "html_promos",
            "url": "https://coupon.example.test/catalog",
            "store": "Test Coupon",
            "enabled": True,
        })
        self.assertTrue(any("Скидка 25% на отель" in deal.title for deal in deals))
        self.assertTrue(any(deal.image_url and deal.product_url.endswith("/promo/hotel") for deal in deals))

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

    def test_expiry_parser_recognizes_end_of_range_after_po(self):
        self.assertEqual(
            _extract_promo_expiry("Акция с 1 октября по 31 октября INABA", today=date(2026, 10, 27)),
            "2026-10-31",
        )

    def test_relative_promo_expiry_is_normalized(self):
        self.assertEqual(
            _extract_promo_expiry("Промокод активен ещё 2 дня", today=date(2026, 10, 27)),
            "2026-10-29",
        )
        self.assertEqual(
            _extract_promo_expiry("Предложение действует 1 неделю", today=date(2026, 10, 27)),
            "2026-11-03",
        )

    def test_expiry_without_year_rolls_over_to_next_year_when_month_is_far_behind(self):
        self.assertEqual(
            _extract_promo_expiry("Промокод действует до 5 января", today=date(2026, 10, 27)),
            "2027-01-05",
        )

    def test_expiry_without_year_keeps_recently_passed_date_in_current_year(self):
        self.assertEqual(
            _extract_promo_expiry("Акция действует до 20 октября", today=date(2026, 10, 27)),
            "2026-10-20",
        )

    def test_parses_aviasales_special_offer_as_ticket_without_fabricating_discount(self):
        payload = json.dumps({
            "success": True, "currency": "rub",
            "data": [{
                "title": "Билет из Москвы в Стамбул", "mini_title": "Горячие билеты",
                "airline": "SU", "airline_title": "Аэрофлот",
                "origin": "MOW", "origin_name": "Москва",
                "destination": "IST", "destination_name": "Стамбул",
                "price": 14500, "departure_at": "2099-01-15T10:30:00+03:00",
                "return_at": "2099-01-22T19:00:00+03:00", "duration": 250,
                "link": "/MOW1501IST1", "search_id": "ticket-search-1",
            }], "error": None,
        }).encode("utf-8")
        deals = parse_aviasales_payload(payload, {
            "name": "Aviasales — тест", "type": "aviasales_api",
            "url": "https://api.travelpayouts.com/aviasales/v3/get_special_offers",
            "origin": "MOW", "enabled": True,
        })
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertEqual(deal.offer_type, "flight")
        self.assertEqual(deal.price, 14500)
        self.assertIsNone(deal.old_price)
        self.assertIsNone(deal.discount_percent)
        self.assertEqual(deal.product_url, "https://www.aviasales.ru/search/MOW1501IST1")
        self.assertEqual(deal.image_url, "https://pics.avs.io/340/100/SU.png")
        self.assertIn("Вылет: 2099-01-15", deal.description)

    def test_aviasales_parser_rejects_non_aviasales_absolute_links(self):
        payload = json.dumps({"success": True, "currency": "rub", "data": [{
            "title": "Ticket", "airline": "SU", "price": 14500,
            "link": "https://evil.example/collect", "departure_at": "2099-01-15T10:30:00Z",
        }]}).encode("utf-8")
        self.assertEqual(parse_aviasales_payload(payload, {"name": "test", "type": "aviasales_api"}), [])

    def test_html_promotions_can_link_travel_offer_to_listing_not_external_redirect(self):
        payload = """
        <html><body>
          <article class="travel-card">
            <h3>Промокод Победа на авиабилеты — скидка 10%</h3>
            <img src="https://img.example.test/pobeda.jpg" alt="Авиабилеты Победа">
            <p>Код действует на тарифы Максимум и Выгодный.</p>
            <a href="https://airline.example.test/redirect?erid=abc">Открыть акцию</a>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "БериКод — тест путешествий",
            "type": "html_promos",
            "url": "https://berikod.example.test/travel/",
            "store": "БериКод — путешествия",
            "enabled": True,
            "fallback_to_source_page": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].product_url, "https://berikod.example.test/travel/")
        self.assertIn("Победа", deals[0].title)
        self.assertEqual(deals[0].reported_discount_percent, 10)
        self.assertIsNone(deals[0].affiliate_url)

    def test_html_promotions_filter_unrelated_ads_from_travel_pages(self):
        payload = """
        <html><body>
          <article class="card">
            <h3>ВК реклама промокод 2026 на первую рекламную кампанию со скидкой 40%</h3>
            <img src="https://img.example.test/ads.jpg" alt="Реклама">
            <p>На запуск рекламного кабинета для новых пользователей.</p>
            <a href="https://ads.example.test/redirect">Открыть акцию</a>
          </article>
          <article class="card">
            <h3>Промокод Победа на авиабилеты — скидка 10%</h3>
            <img src="https://img.example.test/pobeda.jpg" alt="Авиабилеты">
            <p>Код действует на тарифы Максимум и Выгодный.</p>
            <a href="https://airline.example.test/redirect?erid=abc">Открыть акцию</a>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-travel-coupons",
            "type": "html_promos",
            "url": "https://berikod.example.test/travel/",
            "store": "Travel",
            "enabled": True,
            "fallback_to_source_page": True,
            "required_title_keywords": ["авиабилет", "отел", "путешеств", "тур", "поезд"],
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertIn("Победа", deals[0].title)

    def test_html_promotions_fallback_filters_excluded_offer_keywords(self):
        payload = '''
        <html><body>
          <article id="card-travel">
            <h3>Путешествие: скидка 20% на отель</h3>
            <img src="https://img.example.test/travel.jpg" alt="Отель">
            <p>Промо на бронирование отеля в поездке.</p>
          </article>
          <article id="card-loan">
            <h3>Кредит онлайн: скидка 30% на проценты</h3>
            <img src="https://img.example.test/loan.jpg" alt="Кредит">
            <p>Специальное предложение по кредиту.</p>
          </article>
        </body></html>
        '''.encode("utf-8")
        source = {
            "name": "test-picodi-style-source",
            "type": "html_promos",
            "url": "https://coupon.example.test/top",
            "store": "Test",
            "enabled": True,
            "fallback_to_source_page": True,
            "excluded_offer_keywords": ["кредит", "займ"],
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertIn("Путешествие", deals[0].title)
        self.assertTrue(deals[0].product_url.endswith("#card-travel"))

    def test_promo_expiry_parser_accepts_colon_after_do(self):
        self.assertEqual(
            _extract_promo_expiry("Акция активна до: 31.10.2026", today=date(2026, 10, 27)),
            "2026-10-31",
        )
        self.assertEqual(
            _extract_promo_expiry("Промокод активен до: 3 ноября 2026", today=date(2026, 10, 27)),
            "2026-11-03",
        )

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


    def test_rejects_generic_store_index_with_unrelated_percentage_and_code(self):
        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/store/foxford-ru">Все промокоды Фоксфорд (8)</a>
            <img src="/images/foxford.jpg" alt="Фоксфорд">
            <span>Скидка 52%</span>
            <code>LANG50</code>
          </article>
        </body></html>
        """.encode()
        source = {
            "name": "test-coupon-directory",
            "type": "html_promos",
            "url": "https://coupons.example.test/",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])

    def test_keeps_offer_anchor_inside_store_page(self):
        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/store/rigla-ru#promo-1461">Скидки до 60% на товары в подборке</a>
            <img src="/images/rigla.jpg" alt="Подборка товаров">
            <span>Скидки до 60% на выбранные товары</span>
          </article>
        </body></html>
        """.encode()
        source = {
            "name": "test-anchored-promo",
            "type": "html_promos",
            "url": "https://coupons.example.test/",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].reported_discount_percent, 60)
        self.assertTrue(deals[0].discount_is_maximum)
        self.assertIn("#promo-1461", deals[0].product_url)


    def test_html_promotions_rejects_internal_tracking_and_blog_redirects(self):
        blocked_paths = [
            "/api/click-product/621375",
            "/go/cmpwkdo9u004t3clh0m0i3acn",
            "/blog/rasprodazha-11-11-how-to-check",
        ]
        for path in blocked_paths:
            with self.subTest(path=path):
                payload = f"""
                <html><body>
                  <article class="coupon-card">
                    <a href="{path}">Скидка 35% на выбранные товары</a>
                    <img src="/images/offer.jpg" alt="Скидка">
                    <span>Скидка 35%</span>
                  </article>
                </body></html>
                """.encode()
                source = {
                    "name": "test-tracking-rejection",
                    "type": "html_promos",
                    "url": "https://coupons.example.test/",
                    "enabled": True,
                }
                self.assertEqual(parse_feed(payload, source), [])


    def test_rejects_generic_doncoupon_store_page_with_promocode_count(self):
        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/ru-ru/store/letual/coupons">Лэтуаль letu.ru 9 промокодов 9 промокодов</a>
            <img src="/images/letual.jpg" alt="Лэтуаль">
            <span>Промокоды магазина</span>
          </article>
        </body></html>
        """.encode()
        source = {
            "name": "test-doncoupon",
            "type": "html_promos",
            "url": "https://coupons.example.test/",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])

    def test_keeps_doncoupon_individual_coupon_query(self):
        payload = """
        <html><body>
          <article class="coupon-card">
            <a href="/ru-ru/store/befree/coupons?coupon_id=3670">Распродажа мужской коллекции — скидки до 80%</a>
            <img src="/images/befree.jpg" alt="Распродажа">
            <span>Скидки до 80% на мужскую коллекцию</span>
          </article>
        </body></html>
        """.encode()
        source = {
            "name": "test-doncoupon-offer",
            "type": "html_promos",
            "url": "https://coupons.example.test/",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].reported_discount_percent, 80)
        self.assertTrue(deals[0].discount_is_maximum)
        self.assertIn("coupon_id=3670", deals[0].product_url)


    def test_parses_vkusvill_loyalty_card_price_pair(self):
        payload = """
        <html><body>
          <main>
            <div class="ProductCard">
              <a href="/goods/mors-vishnevyy-1l">
                <img src="/images/mors.jpg" alt="Морс вишневый, 1 л">
              </a>
              <a href="/goods/mors-vishnevyy-1l">Морс вишневый, 1 л</a>
              <div class="ProductCard__priceBlock">
                182 руб /шт <s>202 руб</s> 182 202
                Скидка по карте лояльности по 01.11
              </div>
            </div>
          </main>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-vkusvill",
            "type": "html",
            "url": "https://vkusvill.ru/offers/",
            "store": "ВкусВилл",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].title, "Морс вишневый, 1 л")
        self.assertEqual(deals[0].product_url, "https://vkusvill.ru/goods/mors-vishnevyy-1l")
        self.assertEqual(deals[0].price, 182.0)
        self.assertEqual(deals[0].old_price, 202.0)


    def test_html_catalog_prefers_image_alt_when_link_contains_price_and_rating(self):
        payload = """
        <html><body>
          <main>
            <div class="product-card">
              <a href="/product/123">
                <img src="/images/cheeseburger.jpg" alt="Чизбургер 210г">
              </a>
              <a href="/product/123">
                Финальная цена 79,99 ₽ -66% 239,99 ₽
                Чизбургер 210г 4.5 · 11 отзывов В корзину
              </a>
            </div>
          </main>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-retailer",
            "type": "html",
            "url": "https://retailer.example.test/",
            "store": "Тестовый магазин",
            "enabled": True,
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].title, "Чизбургер 210г")
        self.assertEqual(deals[0].price, 79.99)
        self.assertEqual(deals[0].old_price, 239.99)


    def test_html_catalog_rejects_container_prices_that_conflict_with_max_discount(self):
        payload = """
        <html><body>
          <main>
            <div class="sale-list">
              <div class="product-card">
                <a href="/product/miska">
                  <img src="/images/miska.jpg" alt="Миска для животных">
                </a>
                <a href="/product/miska">Миска для животных</a>
                <span>Скидка до 20% · -20%</span>
                <span>819 ₽ 5 697 ₽</span>
              </div>
            </div>
          </main>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-bad-container",
            "type": "html",
            "url": "https://store.example.test/sale/",
            "store": "Тестовый магазин",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])

    def test_html_catalog_rejects_listing_page_links_even_with_query(self):
        payload = """
        <html><body>
          <main>
            <div class="sale-card">
              <a href="/catalog/sale/?page=1">
                Распродажа -99%
                <img src="/images/sale.jpg" alt="Распродажа">
              </a>
              <span>-99% 1,90 ₽ 200 879 ₽</span>
            </div>
          </main>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "test-listing-page",
            "type": "html",
            "url": "https://store.example.test/catalog/sale/",
            "store": "Тестовый магазин",
            "enabled": True,
        }
        self.assertEqual(parse_feed(payload, source), [])


    def test_avianity_parser_extracts_recent_flight_fare_without_fake_discount(self):
        payload = """
        <html><body>
          <article id="post-1">
            <h2 class="entry-title">
              <a href="/specials/aktsiya-pobedy-moskva-antalya/">Акция Победы Москва – Анталья за 3 536 руб. на октябрь 2026</a>
            </h2>
            <div class="entry-meta">27.10.2026 Авианити Акции авиакомпаний</div>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "Avianity test",
            "type": "html_travel_articles",
            "url": "https://avianity.ru/specials/",
            "store": "Авианити",
            "enabled": True,
            "max_article_age_days": 3,
        }
        deals = parse_html_travel_articles(payload, source, today=date(2026, 10, 28))
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].title, "Акция Победы Москва – Анталья за 3 536 руб. на октябрь 2026")
        self.assertEqual(deals[0].price, 3536.0)
        self.assertEqual(deals[0].product_url, "https://avianity.ru/specials/aktsiya-pobedy-moskva-antalya/")
        self.assertEqual(deals[0].offer_type, "flight")
        self.assertIsNone(deals[0].old_price)
        self.assertIsNone(deals[0].discount_percent)

    def test_avianity_parser_skips_old_articles_and_unpriced_hotel_promos(self):
        payload = """
        <html><body>
          <article>
            <h2><a href="/specials/old-fare/">Акция Победы Москва – Сочи за 2 344 руб. на октябрь 2026</a></h2>
            <div class="entry-meta">20.10.2026</div>
          </article>
          <article>
            <h2><a href="/specials/hotel-promo/">Горящий промокод Яндекс Путешествий: скидка 20% на отели в 2027</a></h2>
            <div class="entry-meta">27.10.2026</div>
          </article>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "Avianity test",
            "type": "html_travel_articles",
            "url": "https://avianity.ru/specials/",
            "max_article_age_days": 3,
        }
        self.assertEqual(
            parse_html_travel_articles(payload, source, today=date(2026, 10, 28)),
            [],
        )

    def test_extracts_https_open_graph_image_for_travel_article(self):
        payload = """
        <html><head>
          <meta property="og:image" content="/wp-content/uploads/fare-preview.jpg">
        </head><body></body></html>
        """.encode("utf-8")
        self.assertEqual(
            _extract_open_graph_image(payload, "https://avianity.ru/specials/sample-fare/"),
            "https://avianity.ru/wp-content/uploads/fare-preview.jpg",
        )

    def test_open_graph_image_rejects_insecure_http_image(self):
        payload = """
        <html><head>
          <meta property="og:image" content="http://images.example.test/fare.jpg">
        </head></html>
        """.encode("utf-8")
        self.assertIsNone(
            _extract_open_graph_image(payload, "https://avianity.ru/specials/sample-fare/"),
        )


    def test_tutu_tour_parser_extracts_price_pair_direct_link_and_image(self):
        payload = """
        <html><body>
          <div class="kite-panel kite-panel-container">
            <a href="/tour/istanbul-123/">
              <img src="https://cdn.tutu.ru/hotels/istanbul.jpg" alt="Отель">
            </a>
            <div class="hotel-name">Galata Hotel Suites</div>
            <div>Стамбул, Турция · 3 ночи · 2 взрослых · Кешбэк до 7% от 51 195 ₽ 59 310 ₽ - 14%</div>
          </div>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "Tutu test",
            "type": "html_tour_cards",
            "url": "https://tours.tutu.ru/hot_tours/",
            "store": "Туту",
            "enabled": True,
        }
        deals = parse_html_tour_cards(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].title, "Galata Hotel Suites")
        self.assertEqual(deals[0].price, 51195.0)
        self.assertEqual(deals[0].old_price, 59310.0)
        self.assertEqual(deals[0].offer_type, "tour")
        self.assertEqual(deals[0].product_url, "https://tours.tutu.ru/tour/istanbul-123/")
        self.assertEqual(deals[0].image_url, "https://cdn.tutu.ru/hotels/istanbul.jpg")

    def test_generic_tour_card_parser_accepts_verified_discounted_tour_card(self):
        payload = """
        <html><body>
          <article class="tour-card">
            <a href="/tour/altai-summer/"><img src="https://cdn.example.test/altai.jpg" alt="Алтай"></a>
            <h3 class="tour-title">Тур по Алтаю: 7 дней</h3>
            <div class="tour-price">49 900 ₽ 62 375 ₽ − 20%</div>
          </article>
        </body></html>
        """.encode()
        source = {
            "name": "Test tour provider",
            "type": "html_tour_cards",
            "url": "https://tours.example.test/specials/",
            "store": "Тестовые туры",
        }
        deals = parse_html_tour_cards(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].title, "Тур по Алтаю: 7 дней")
        self.assertEqual(deals[0].price, 49900.0)
        self.assertEqual(deals[0].old_price, 62375.0)
        self.assertEqual(deals[0].reported_discount_percent, 20.0)
        self.assertEqual(deals[0].product_url, "https://tours.example.test/tour/altai-summer/")
        self.assertEqual(deals[0].offer_type, "tour")

    def test_generic_tour_card_parser_rejects_unverified_prices_and_external_links(self):
        payload = """
        <html><body>
          <article class="tour-card">
            <a href="https://other.example.test/tour/altai/"><img src="https://cdn.example.test/altai.jpg"></a>
            <h3>Тур по Алтаю</h3>
            <div>49 900 ₽ 62 375 ₽ − 70%</div>
          </article>
        </body></html>
        """.encode()
        source = {"name": "Test", "type": "html_tour_cards", "url": "https://tours.example.test/"}
        self.assertEqual(parse_html_tour_cards(payload, source), [])

    def test_tutu_tour_parser_rejects_mismatched_discount_and_listing_links(self):
        payload = """
        <html><body>
          <div class="kite-panel kite-panel-container">
            <a href="/hot_tours/"><img src="https://cdn.tutu.ru/hotels/list.jpg"></a>
            <div class="hotel-name">Hotel Listing</div>
            <div>от 51 195 ₽ 59 310 ₽ - 60%</div>
          </div>
        </body></html>
        """.encode("utf-8")
        source = {
            "name": "Tutu test",
            "type": "html_tour_cards",
            "url": "https://tours.tutu.ru/hot_tours/",
        }
        self.assertEqual(parse_html_tour_cards(payload, source), [])

if __name__ == "__main__":
    unittest.main()
