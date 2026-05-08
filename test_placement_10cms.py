#!/usr/bin/env python3
"""Sweep 10 well-known CMS sites with Playwright and report placement classification.

Identifies edge cases by comparing expected vs actual classifications.
"""
import sys
from bs4 import BeautifulSoup
from src.core.link_manager import LinkManager, PLACEMENT_TO_CATEGORY
from urllib.parse import urlparse

SITES = [
    # (URL, CMS, Expected patterns to watch for)
    ('https://techcrunch.com/', 'WordPress', 'Gutenberg blocks, nav menus, widget areas'),
    ('https://www.allbirds.com/', 'Shopify', 'product cards, mega menu, announcement bar'),
    ('https://ghost.org/blog/', 'Ghost', 'gh-*/kg-* classes, post-cards'),
    ('https://webflow.com/', 'Webflow', 'w-nav, w-button, w-dyn-item'),
    ('https://www.hubspot.com/', 'HubSpot', 'hs-menu, hs-cta, hs-button'),
    ('https://www.drupal.org/', 'Drupal', 'node--, views-row, primary-nav'),
    ('https://www.joomla.org/', 'Joomla', 'Bootstrap 5, mod-menu, navbar'),
    ('https://magento.com/', 'Magento/Adobe', 'action.primary, nav-sections, product-item'),
    ('https://vercel.com/', 'Next.js/Vercel', 'Tailwind utilities, data-* attributes'),
    ('https://www.squarespace.com/', 'Squarespace', 'sqs-block, header-nav, summary-item'),
]


def fetch_with_playwright(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
        )
        page = context.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()
    return html


def analyze_site(url, cms_name, notes):
    domain = urlparse(url).netloc.replace('www.', '', 1)

    try:
        html = fetch_with_playwright(url)
    except Exception as e:
        return {'cms': cms_name, 'url': url, 'error': str(e)}

    soup = BeautifulSoup(html, 'html.parser')
    lm = LinkManager(domain)

    links = soup.find_all('a', href=True)
    total_tags = len(links)

    counts = {}
    cat_counts = {}
    edge_cases = []

    for link in links:
        href = link.get('href', '')
        if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:') or href.startswith('javascript:'):
            continue

        placement, detail = lm.classify_link_placement(link)
        counts[detail] = counts.get(detail, 0) + 1
        cat_counts[placement] = cat_counts.get(placement, 0) + 1

        anchor = link.get_text(strip=True)[:50]

        # Flag potential edge cases
        # 1. body_paragraph with no parent <p> (might be misclassified)
        if detail == 'body_paragraph':
            parent = link.parent
            if parent and parent.name not in ('p', 'li', 'td', 'div', 'span', 'main', 'article', 'section', 'body'):
                edge_cases.append(f"body_paragraph in <{parent.name}>: '{anchor[:30]}' → {href[:50]}")

        # 2. header but not actually in semantic <header>
        if placement == 'header':
            has_header_tag = False
            cur = link.parent
            while cur and cur.name:
                if cur.name == 'header':
                    has_header_tag = True
                    break
                cur = cur.parent
            if not has_header_tag:
                edge_cases.append(f"header without <header> tag: '{anchor[:30]}' → {href[:50]}")

        # 3. footer but not in semantic <footer>
        if placement == 'footer':
            has_footer_tag = False
            cur = link.parent
            while cur and cur.name:
                if cur.name == 'footer':
                    has_footer_tag = True
                    break
                cur = cur.parent
            if not has_footer_tag:
                edge_cases.append(f"footer without <footer> tag: '{anchor[:30]}' → {href[:50]}")

        # 4. nav_main with very long anchor text (probably body content misclassified)
        if detail == 'nav_main' and len(anchor) > 40:
            edge_cases.append(f"nav_main long anchor: '{anchor[:50]}' → {href[:50]}")

    total_classified = sum(counts.values())

    return {
        'cms': cms_name,
        'url': url,
        'notes': notes,
        'total_a_tags': total_tags,
        'total_classified': total_classified,
        'categories': cat_counts,
        'details': counts,
        'edge_cases': edge_cases[:10],  # cap at 10
    }


def main():
    results = []
    for url, cms, notes in SITES:
        print(f"\n{'='*70}")
        print(f"  {cms}: {url}")
        print(f"{'='*70}")

        result = analyze_site(url, cms, notes)
        results.append(result)

        if 'error' in result:
            print(f"  ERROR: {result['error']}")
            continue

        # Print category summary
        total = result['total_classified']
        print(f"  Links: {result['total_a_tags']} tags, {total} classified")
        print()
        for cat in ['body', 'nav', 'footer', 'header']:
            c = result['categories'].get(cat, 0)
            pct = f"{c/total*100:.0f}%" if total else "0%"
            print(f"    {cat:<12} {c:>4}  ({pct})")

        # Print detail breakdown
        print()
        for detail, count in sorted(result['details'].items(), key=lambda x: -x[1]):
            print(f"    {detail:<25} {count:>4}")

        # Print edge cases
        if result['edge_cases']:
            print(f"\n  EDGE CASES ({len(result['edge_cases'])}):")
            for ec in result['edge_cases']:
                print(f"    ⚠  {ec}")

    # Final summary
    print(f"\n\n{'='*70}")
    print(f"  CROSS-CMS SUMMARY")
    print(f"{'='*70}")
    print(f"\n  {'CMS':<20} {'Links':<8} {'body':<8} {'nav':<8} {'footer':<8} {'header':<8} {'Edge Cases'}")
    print(f"  {'-'*80}")
    for r in results:
        if 'error' in r:
            print(f"  {r['cms']:<20} ERROR: {r['error'][:50]}")
            continue
        cats = r['categories']
        ec = len(r['edge_cases'])
        print(f"  {r['cms']:<20} {r['total_classified']:<8} {cats.get('body',0):<8} {cats.get('nav',0):<8} {cats.get('footer',0):<8} {cats.get('header',0):<8} {ec}")

    # Aggregate edge cases
    all_edge = []
    for r in results:
        if 'edge_cases' in r:
            for ec in r['edge_cases']:
                all_edge.append(f"[{r['cms']}] {ec}")

    if all_edge:
        print(f"\n  ALL EDGE CASES ({len(all_edge)}):")
        for ec in all_edge:
            print(f"    ⚠  {ec}")


if __name__ == '__main__':
    main()
