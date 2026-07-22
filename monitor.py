#!/usr/bin/env python3
"""Aosom UK air-conditioner stock monitor.

Uses only the Python standard library. It discovers products from Aosom's Air
Conditioning category, follows category pagination, compares current availability
with state.json, and sends Discord alerts for restocks or new in-stock products.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path.name}: {exc}") from exc


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch(url: str, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"Could not download {url}: {last_error}")


def normalise_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b[^>]*>.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def product_title(anchor_html: str, href: str) -> str:
    for attr in ("title", "aria-label"):
        match = re.search(rf'\b{attr}=["\']([^"\']+)["\']', anchor_html, flags=re.I)
        if match:
            text = html.unescape(match.group(1)).strip()
            if len(text) > 8:
                return text
    text = strip_tags(anchor_html)
    if len(text) > 8:
        return text
    slug = urlparse(href).path.rsplit("/", 1)[-1].rsplit("~", 1)[0]
    return re.sub(r"[-_]+", " ", slug).strip().title()


def wanted_product(title: str, include: list[str], exclude: list[str]) -> bool:
    lowered = title.casefold()
    if not any(term.casefold() in lowered for term in include):
        return False
    # Keep true AC products even if their long name also mentions dehumidifier mode.
    if "air conditioner" in lowered or "air conditioning unit" in lowered:
        return "air cooler" not in lowered
    return not any(term.casefold() in lowered for term in exclude)


def discover_page_links(page_html: str, base_url: str) -> set[str]:
    links = {normalise_url(base_url)}
    category_path = urlparse(base_url).path.rstrip("/")
    for href in re.findall(r'href=["\']([^"\']+)["\']', page_html, flags=re.I):
        absolute = urljoin(base_url, html.unescape(href))
        parsed = urlparse(absolute)
        if parsed.netloc == urlparse(base_url).netloc and parsed.path.rstrip("/") == category_path:
            # Retain query strings because Aosom may encode pagination there.
            links.add(urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, "")))
    return links


def parse_products(page_html: str, page_url: str, include: list[str], exclude: list[str]) -> dict[str, dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r'<a\b(?P<attrs>[^>]*?href=["\'](?P<href>[^"\']*(?:/item/)[^"\']+)["\'][^>]*)>'
        r'(?P<body>.*?)</a>',
        flags=re.I | re.S,
    )
    matches = list(pattern.finditer(page_html))
    for match in matches:
        href = html.unescape(match.group("href"))
        url = normalise_url(urljoin(page_url, href))
        anchor_html = match.group(0)
        title = product_title(anchor_html, url)
        if not wanted_product(title, include, exclude):
            continue

        # Inspect the surrounding product-card region. Aosom places “Out of stock”
        # immediately before the product link in its catalogue cards.
        start = max(0, match.start() - 1400)
        end = min(len(page_html), match.end() + 2200)
        context = strip_tags(page_html[start:end]).casefold()
        anchor_pos = context.find(title.casefold()[:30])
        nearby = context[max(0, anchor_pos - 400): anchor_pos + 900] if anchor_pos >= 0 else context

        out_signals = ("out of stock", "notify me when available", "insufficient stock")
        in_signals = ("add to basket", "add to cart", "in stock", "buy now")
        is_out = any(signal in nearby for signal in out_signals)
        is_in = any(signal in nearby for signal in in_signals)
        available = bool(is_in and not is_out)
        reason = "out-of-stock wording" if is_out else ("purchase/in-stock wording" if is_in else "no positive stock signal")

        current = products.get(url)
        candidate = {"title": title, "url": url, "available": available, "reason": reason}
        # Prefer the longest title and any positive availability reading.
        if current is None or len(title) > len(current["title"]) or available:
            products[url] = candidate
    return products


def send_discord(webhook_url: str, content: str) -> None:
    payload = json.dumps({"content": content}).encode("utf-8")
    req = Request(webhook_url, data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    try:
        with urlopen(req, timeout=20) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord returned HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Discord notification failed: {exc}") from exc


def main() -> int:
    config = load_json(CONFIG_PATH, {})
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    test_mode = os.environ.get("TEST_NOTIFICATION", "false").lower() in {"1", "true", "yes", "on"}

    if not webhook:
        print("ERROR: DISCORD_WEBHOOK_URL is missing.", file=sys.stderr)
        return 1
    if test_mode:
        send_discord(webhook, "✅ Test successful — your cloud Aosom air-conditioner monitor can send Discord alerts.")
        print("Test notification sent.")
        return 0

    base_url = config.get("category_url")
    include = config.get("include_keywords", ["air conditioner", "air conditioning unit"])
    exclude = config.get("exclude_keywords", ["air cooler"])
    if not base_url:
        print("ERROR: category_url is missing from config.json", file=sys.stderr)
        return 1

    first_html = fetch(base_url)
    page_urls = discover_page_links(first_html, base_url)
    queue = list(sorted(page_urls))
    visited: set[str] = set()
    products: dict[str, dict[str, Any]] = {}

    while queue and len(visited) < 10:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        page_html = first_html if normalise_url(page_url) == normalise_url(base_url) and not urlparse(page_url).query else fetch(page_url)
        products.update(parse_products(page_html, page_url, include, exclude))
        for discovered in discover_page_links(page_html, base_url):
            if discovered not in visited and discovered not in queue:
                queue.append(discovered)

    if not products:
        print("ERROR: No qualifying air-conditioner products were discovered. The site layout may have changed.", file=sys.stderr)
        return 1

    previous = load_json(STATE_PATH, {})
    first_run = not bool(previous)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_state: dict[str, Any] = {}
    alerts: list[dict[str, Any]] = []

    for url, product in sorted(products.items(), key=lambda pair: pair[1]["title"]):
        old = previous.get(url)
        available = bool(product["available"])
        new_state[url] = {
            "title": product["title"],
            "available": available,
            "last_checked": now,
            "reason": product["reason"],
        }
        if first_run:
            continue
        was_available = bool(old.get("available")) if isinstance(old, dict) else False
        is_new = old is None
        if available and (not was_available) and (not is_new or config.get("alert_on_new_in_stock_products", True)):
            alerts.append({**product, "is_new": is_new})

    # Preserve old entries temporarily if a pagination/page request omits a product.
    for url, old in previous.items():
        if url not in new_state and isinstance(old, dict):
            preserved = dict(old)
            preserved["last_checked"] = now
            preserved["missing_from_category"] = True
            new_state[url] = preserved

    save_json(STATE_PATH, new_state)

    for product in alerts:
        event = "NEW IN-STOCK PRODUCT" if product["is_new"] else "RESTOCK DETECTED"
        send_discord(
            webhook,
            f"🚨 **AOSOM {event}**\n\n"
            f"**{product['title']}**\n"
            f"🟢 Status: In stock\n\n"
            f"🔗 {product['url']}",
        )

    available_count = sum(1 for p in products.values() if p["available"])
    print(f"Checked {len(products)} qualifying air conditioners across {len(visited)} category page(s).")
    print(f"Currently detected in stock: {available_count}.")
    if first_run:
        print("Initial baseline saved; no stock alerts were sent.")
    else:
        print(f"Alerts sent: {len(alerts)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
