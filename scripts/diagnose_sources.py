from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deal_hunter.sources import (  # noqa: E402
    _HtmlTreeParser,
    _PRICE_TOKEN,
    _html_images,
    _html_links,
    _html_text,
    _html_walk,
    _html_price_pair,
    _decode_html_payload,
    parse_feed,
)
from deal_hunter.models import clean_text  # noqa: E402


def _read_config() -> list[dict]:
    path = PROJECT_ROOT / "config" / "sources.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    return [item for item in sources if isinstance(item, dict) and (item.get("enabled") is True or item.get("diagnostic") is True)]


def inspect_source(source: dict) -> None:
    name = str(source.get("name") or "unnamed")
    url = str(source.get("url") or "")
    mode = "ENABLED" if source.get("enabled") is True else "DIAGNOSTIC ONLY — NOT ACTIVE IN THE BOT"
    print(f"\n===== {mode}: {name} =====", flush=True)
    if source.get("type") == "aviasales_api":
        print("[API SOURCE] Requires AVIASALES_API_TOKEN; actual ticket-price API is checked by the Deal Hunter scan.")
        return
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DealHunterBot/0.1 (+public deal page reader)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
            content_type = response.headers.get("Content-Type", "unknown")
            status = response.status
    except urllib.error.HTTPError as exc:
        print(f"[HTTP ERROR] status={exc.code}")
        return
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[FETCH ERROR] {type(exc).__name__}: {getattr(exc, 'reason', 'timeout')}")
        return

    if len(payload) > 2 * 1024 * 1024:
        print("[LIMIT] Page exceeded 2 MiB diagnostic sample")
        payload = payload[:2 * 1024 * 1024]

    print(f"[FETCH OK] status={status}; bytes={len(payload)}; content_type={content_type}", flush=True)
    parser = _HtmlTreeParser()
    parser.feed(_decode_html_payload(payload))
    parser.close()

    title = ""
    for node in _html_walk(parser.root):
        if node.tag == "title":
            title = _html_text(node)[:160]
            break
    print(f"Page title: {title or '(not found)'}")

    try:
        deals = parse_feed(payload, source, max_items=30)
    except Exception as exc:
        print(f"[PARSER ERROR] {type(exc).__name__}: {exc}")
        deals = []
    print(f"Deals extracted by production parser: {len(deals)}")
    for deal in deals[:3]:
        discount_label = (
            f"{'up to ' if deal.discount_is_maximum else ''}{deal.discount_percent:.1f}%"
            if deal.discount_percent is not None
            else "no stated %"
        )
        print(
            f"  [DEAL] {discount_label} | now={deal.price} | old={deal.old_price} | "
            f"code={deal.promo_code or 'none'} | title={deal.title[:120]} | "
            f"image={'yes' if deal.image_url else 'no'} | url={deal.product_url[:180]}"
        )

    nodes = list(_html_walk(parser.root))
    # Build all element text once in reverse document order. Re-running a recursive
    # text walk for every ancestor is quadratic on large retail pages.
    text_by_id: dict[int, str] = {}
    ignored = {"script", "style", "noscript", "svg"}
    for node in reversed(nodes):
        parts: list[str] = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag not in ignored:
                parts.append(text_by_id.get(id(child), ""))
        text_by_id[id(node)] = clean_text(" ".join(parts), 5000)

    candidates = []
    for node in reversed(nodes):
        if node.tag in {"document", "html", "body", "main", "script", "style", "nav", "footer", "header"}:
            continue
        text = text_by_id.get(id(node), "")
        if len(text) < 25 or len(text) > 1200:
            continue
        money_count = len(re.findall(_PRICE_TOKEN, text, flags=re.IGNORECASE))
        has_discount = bool(re.search(r"(?:-\s*|\(\s*)\d{1,2}\s*%(?:\s*\))?", text))
        if money_count < 2 or not has_discount:
            continue
        links = _html_links(node, url)
        images = _html_images(node, url)
        if len(links) > 12:
            links = links[:12]
        class_name = node.attrs.get("class", "")[:100]
        pair = _html_price_pair(text)
        candidates.append((node.tag, class_name, text, links, images, pair))
        if len(candidates) >= 4:
            break

    print(f"Small HTML blocks with 2+ prices and a percentage marker: {len(candidates)} sampled")
    for tag, class_name, text, links, images, pair in candidates:
        print(
            f"  [BLOCK] tag={tag} class={class_name!r} prices={pair} links={len(links)} images={len(images)} "
            f"text={text[:260]!r}"
        )
        if links:
            print(f"    first_link={links[0][0][:180]!r} label={links[0][1][:100]!r}")
        if images:
            print(f"    first_image={images[0][:180]!r}")

    if not candidates and not deals:
        money_samples = []
        for node in reversed(nodes):
            if node.tag in {"document", "html", "body", "main", "script", "style", "nav", "footer", "header"}:
                continue
            text = text_by_id.get(id(node), "")
            if 30 <= len(text) <= 300 and re.search(_PRICE_TOKEN, text, flags=re.IGNORECASE):
                money_samples.append((node, text))
                if len(money_samples) >= 3:
                    break
        for node, text in money_samples:
            tag = node.tag
            class_name = node.attrs.get("class", "")[:80]
            print(f"  [PRICE SAMPLE] tag={tag} class={class_name!r} text={text[:220]!r}")
            links = _html_links(node, url)
            images = _html_images(node, url)
            if links:
                print(f"    first_link={links[0][0][:180]!r} label={links[0][1][:100]!r}")
            if images:
                print(f"    first_image={images[0][:180]!r}")
            ancestor = node.parent
            depth = 1
            while ancestor is not None and depth <= 3:
                ancestor_text = text_by_id.get(id(ancestor), "")
                ancestor_links = _html_links(ancestor, url)
                ancestor_images = _html_images(ancestor, url)
                print(
                    f"    [ANCESTOR {depth}] tag={ancestor.tag} id={ancestor.attrs.get('id', '')!r} "
                    f"class={ancestor.attrs.get('class', '')[:100]!r} "
                    f"text={ancestor_text[:180]!r} links={len(ancestor_links)} images={len(ancestor_images)}"
                )
                if ancestor_links:
                    print(f"      ancestor_link={ancestor_links[0][0][:180]!r} label={ancestor_links[0][1][:100]!r}")
                ancestor = ancestor.parent
                depth += 1


def main() -> int:
    sources = _read_config()
    print("===== PUBLIC DEAL SOURCE DIAGNOSTICS (READ-ONLY) =====")
    print(f"Source pages checked: {len(sources)}")
    print(f"Production-enabled sources: {sum(1 for item in sources if item.get('enabled') is True)}")
    print(f"Diagnostic-only candidates: {sum(1 for item in sources if item.get('enabled') is not True)}")
    print("No Telegram methods are called. This workflow only downloads public pages and inspects visible product/price markup.")
    for source in sources:
        inspect_source(source)
    print("\nDiagnostics complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
