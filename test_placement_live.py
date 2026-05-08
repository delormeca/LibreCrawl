#!/usr/bin/env python3
"""Quick local test: classify every link on a page and print a table.

Usage:
    python3 test_placement_live.py https://example.com          # requests (no JS)
    python3 test_placement_live.py https://example.com --js      # Playwright (full JS)
    python3 test_placement_live.py /path/to/saved.html
"""
import sys
import requests
from bs4 import BeautifulSoup
from src.core.link_manager import LinkManager, PLACEMENT_TO_CATEGORY
from urllib.parse import urlparse


def fetch_with_playwright(url):
    """Render page with Playwright (full JS + stealth)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
        )
        page = context.new_page()
        print(f"Fetching {url} with Playwright (JS rendering)...")
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        # Wait for JS to render dynamic content
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()
    return html


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_placement_live.py <url_or_file> [--js]")
        sys.exit(1)

    source = sys.argv[1]
    use_js = '--js' in sys.argv

    if source.startswith('http'):
        if use_js:
            html = fetch_with_playwright(source)
        else:
            print(f"Fetching {source} (no JS)...")
            resp = requests.get(source, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            html = resp.text
        domain = urlparse(source).netloc.replace('www.', '', 1)
    else:
        with open(source) as f:
            html = f.read()
        domain = 'local.test'

    soup = BeautifulSoup(html, 'html.parser')
    lm = LinkManager(domain)

    links = soup.find_all('a', href=True)
    print(f"\nFound {len(links)} <a> tags on page\n")

    # Header
    print(f"{'Placement':<10} {'Detail':<18} {'Anchor Text':<40} {'URL':<60}")
    print("-" * 130)

    # Counters
    counts = {}
    category_counts = {}

    for link in links:
        href = link.get('href', '')
        if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:') or href.startswith('javascript:'):
            continue

        placement, detail = lm.classify_link_placement(link)
        anchor = link.get_text(strip=True)[:38] or '(no text)'
        url_display = href[:58]

        print(f"{placement:<10} {detail:<18} {anchor:<40} {url_display}")

        counts[detail] = counts.get(detail, 0) + 1
        category_counts[placement] = category_counts.get(placement, 0) + 1

    # Summary
    total = sum(counts.values())
    print(f"\n{'='*60}")
    print(f"BROAD CATEGORY SUMMARY (what internal-linking parser sees)")
    print(f"{'='*60}")
    for cat in ['body', 'nav', 'footer', 'header']:
        c = category_counts.get(cat, 0)
        pct = f"{c/total*100:.0f}%" if total else "0%"
        print(f"  {cat:<12} {c:>4}  ({pct})")

    print(f"\n{'='*60}")
    print(f"PLACEMENT DETAIL SUMMARY")
    print(f"{'='*60}")
    for detail, count in sorted(counts.items(), key=lambda x: -x[1]):
        # Derive broad category
        cat = 'body'
        for prefix in ['nav_', 'footer_', 'header_']:
            if detail.startswith(prefix):
                cat = prefix.rstrip('_')
                break
        if detail.startswith('body'):
            cat = 'body'
        print(f"  {detail:<25} {count:>4}   ({cat})")

    print(f"\n  Total classified: {total}")
    if use_js:
        print(f"  Mode: Playwright (JS rendered)")
    else:
        print(f"  Mode: requests (no JS)")


if __name__ == '__main__':
    main()
