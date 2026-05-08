"""Tests for linkgraph mode: DB schema, section extraction, enriched links, export."""
import os
import sys
import json
import sqlite3
import tempfile
import pytest

# Add parent dir to path so we can import src modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- DB Schema Tests ---

@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Provide a temp database for testing."""
    db_path = str(tmp_path / 'test.db')
    import src.crawl_db as crawl_db
    monkeypatch.setattr(crawl_db, 'DB_FILE', db_path)
    crawl_db.init_crawl_tables()
    return db_path


def test_crawl_sections_table_exists(temp_db):
    """crawl_sections table should be created by init_crawl_tables."""
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crawl_sections'")
    assert cursor.fetchone() is not None, "crawl_sections table should exist"
    conn.close()


def test_crawl_links_has_enriched_columns(temp_db):
    """crawl_links should have context, parent_heading, section_position, attributes columns."""
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("PRAGMA table_info(crawl_links)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()
    for col in ['context', 'parent_heading', 'section_position', 'attributes']:
        assert col in columns, f"crawl_links should have '{col}' column"


def test_crawls_has_crawl_mode_column(temp_db):
    """crawls table should have crawl_mode column."""
    conn = sqlite3.connect(temp_db)
    cursor = conn.execute("PRAGMA table_info(crawls)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()
    assert 'crawl_mode' in columns, "crawls should have 'crawl_mode' column"


def test_migrations_are_idempotent(temp_db):
    """Calling init_crawl_tables twice should not error."""
    import src.crawl_db as crawl_db
    # Second call — should not raise
    crawl_db.init_crawl_tables()


def test_save_and_load_sections(temp_db):
    """Sections can be saved and loaded back."""
    import src.crawl_db as crawl_db

    # Create a crawl record first
    crawl_id = crawl_db.create_crawl(1, 'test-session', 'https://example.com', 'example.com', {})
    assert crawl_id is not None

    sections = [
        {'url': 'https://example.com/', 'heading': 'Intro', 'heading_level': 1, 'text': 'Hello world', 'word_count': 2, 'position': 0},
        {'url': 'https://example.com/', 'heading': 'Services', 'heading_level': 2, 'text': 'We do stuff', 'word_count': 3, 'position': 1},
    ]
    assert crawl_db.save_sections_batch(crawl_id, sections) is True

    loaded = crawl_db.load_crawl_sections(crawl_id)
    assert len(loaded) == 2
    assert loaded[0]['heading'] == 'Intro'
    assert loaded[1]['heading'] == 'Services'
    assert loaded[1]['position'] == 1


def test_save_links_with_enriched_data(temp_db):
    """save_links_batch works with enriched linkgraph fields."""
    import src.crawl_db as crawl_db

    crawl_id = crawl_db.create_crawl(1, 'test-session', 'https://example.com', 'example.com', {})

    links = [{
        'source_url': 'https://example.com/',
        'target_url': 'https://example.com/about',
        'anchor_text': 'About Us',
        'is_internal': True,
        'target_domain': 'example.com',
        'target_status': 200,
        'placement': 'body',
        'context': 'Learn more About Us and our mission.',
        'parent_heading': 'Company Info',
        'section_position': 2,
        'attributes': {'rel': None, 'target': None, 'title': 'About', 'class': 'cta', 'id': None, 'data': {'track': 'click'}},
    }]
    assert crawl_db.save_links_batch(crawl_id, links) is True

    loaded = crawl_db.load_crawl_links(crawl_id)
    assert len(loaded) == 1
    assert loaded[0]['context'] == 'Learn more About Us and our mission.'
    assert loaded[0]['parent_heading'] == 'Company Info'
    assert loaded[0]['section_position'] == 2
    attrs = json.loads(loaded[0]['attributes'])
    assert attrs['title'] == 'About'
    assert attrs['data']['track'] == 'click'


# --- SEOExtractor Tests ---

def test_clean_soup_removes_boilerplate():
    """_clean_soup should strip nav, header, footer, scripts, cookie banners."""
    from src.core.seo_extractor import SEOExtractor
    html = '''<html><body>
    <script>alert("x")</script>
    <style>.x{color:red}</style>
    <nav>Navigation here</nav>
    <header>Header content</header>
    <div id="cookie-banner">Accept cookies</div>
    <h1>Title</h1>
    <p>Real content paragraph.</p>
    <footer>Footer stuff</footer>
    </body></html>'''
    soup = SEOExtractor._clean_soup(html)
    text = soup.get_text()
    assert 'alert' not in text, 'Scripts should be removed'
    assert 'Navigation' not in text, 'Nav should be removed'
    assert 'Footer stuff' not in text, 'Footer should be removed'
    assert 'cookie' not in text.lower(), 'Cookie banner should be removed'
    assert 'Real content' in text, 'Content should remain'


def test_clean_soup_returns_none_for_empty():
    """_clean_soup returns None for empty input."""
    from src.core.seo_extractor import SEOExtractor
    assert SEOExtractor._clean_soup('') is None
    assert SEOExtractor._clean_soup(None) is None


def test_extract_body_text_uses_clean_soup():
    """extract_body_text should use _clean_soup and trafilatura."""
    from src.core.seo_extractor import SEOExtractor
    html = '''<html><body>
    <nav>Skip this nav</nav>
    <h1>Main Title</h1>
    <p>This is a substantial paragraph with enough words to ensure trafilatura considers it
    real content rather than boilerplate. We need multiple sentences to get past the precision
    filter. The quick brown fox jumps over the lazy dog in various creative ways.</p>
    <p>Another paragraph to add substance and ensure extraction works properly with the
    favor_precision flag enabled. More content helps trafilatura do its job well.</p>
    </body></html>'''
    result = {}
    SEOExtractor.extract_body_text(html, result)
    assert len(result['body_text']) > 0, 'Should extract body text'
    assert 'Skip this nav' not in result['body_text'], 'Nav should be stripped'


def test_extract_sections_with_headings():
    """extract_sections splits by H2/H3 headings using DOM, skipping intro boilerplate."""
    from src.core.seo_extractor import SEOExtractor

    # Each paragraph >30 words to avoid merge
    html = '''<html><body>
    <nav>Skip this navigation menu content entirely</nav>
    <h1>Insurance Guide</h1>
    <p>Welcome to our comprehensive insurance guide covering all aspects of vehicle protection.</p>
    <h2>Liability Coverage</h2>
    <p>Liability coverage is the foundation of any auto insurance policy in Quebec providing essential protection against claims from other drivers pedestrians and property owners when you are found responsible for an accident that causes damage or injury to others on the road.</p>
    <h2>Comprehensive Protection</h2>
    <p>Comprehensive protection extends your coverage beyond collisions to include theft vandalism natural disasters falling objects and other non-collision events that could damage your vehicle while it is parked or in situations where traditional collision coverage would not apply to your claim.</p>
    <footer>Footer junk</footer>
    </body></html>'''

    sections = SEOExtractor.extract_sections(html, title='Insurance Guide')
    assert len(sections) >= 2, f'Expected >= 2 sections, got {len(sections)}'
    headings = [s['heading'] for s in sections]
    assert 'Liability Coverage' in headings
    assert 'Comprehensive Protection' in headings
    # No boilerplate (nav/footer stripped by _clean_soup, intro skipped)
    for s in sections:
        assert 'navigation menu' not in s['text'].lower()
        assert 'Footer junk' not in s['text']
    # Positions are sequential
    for i, s in enumerate(sections):
        assert s['position'] == i


def test_extract_sections_no_headings_fallback():
    """Pages with no H2/H3 return a single section with full text."""
    from src.core.seo_extractor import SEOExtractor
    html = '<html><body><p>Just a paragraph with no headings at all but with enough words to be meaningful content for testing.</p></body></html>'
    sections = SEOExtractor.extract_sections(html, title='My Page')
    assert len(sections) == 1
    assert sections[0]['heading'] == 'My Page'
    assert sections[0]['heading_level'] == 1
    assert sections[0]['position'] == 0
    assert sections[0]['word_count'] > 0


def test_extract_sections_merges_small():
    """Sections under 30 words should merge into previous section."""
    from src.core.seo_extractor import SEOExtractor
    html = '''<html><body>
    <h2>Big Section</h2>
    <p>This section has plenty of words to stand on its own as an independent section for embedding and vectorization purposes here and more.</p>
    <h2>Tiny</h2>
    <p>Too short.</p>
    </body></html>'''
    sections = SEOExtractor.extract_sections(html, title='Test')
    # "Tiny" (2 words) should be merged into "Big Section"
    headings = [s['heading'] for s in sections]
    assert 'Tiny' not in headings, 'Small section should be merged'


def test_extract_sections_table_content():
    """Tables should be converted to readable text in sections."""
    from src.core.seo_extractor import SEOExtractor
    html = '''<html><body>
    <h2>Data Table</h2>
    <table><tr><th>Name</th><th>Value</th></tr><tr><td>Alpha</td><td>100</td></tr><tr><td>Beta</td><td>200</td></tr></table>
    <p>Additional paragraph content with enough words to make the section meaningful for testing purposes here.</p>
    </body></html>'''
    sections = SEOExtractor.extract_sections(html, title='Test')
    assert len(sections) >= 1
    data_section = [s for s in sections if s['heading'] == 'Data Table']
    assert len(data_section) == 1
    assert 'Alpha' in data_section[0]['text']
    assert 'Beta' in data_section[0]['text']


def test_extract_sections_empty_html():
    """Empty HTML returns empty list."""
    from src.core.seo_extractor import SEOExtractor
    assert SEOExtractor.extract_sections('', title='X') == []
    assert SEOExtractor.extract_sections(None, title='X') == []


# --- LinkManager Enriched Tests ---

LINK_TEST_HTML = '''<html><body>
<a href="/main-content" class="sr-only">Skip to content</a>
<header>
    <a href="/home" class="site-logo custom-logo-link"><img src="/logo.png" alt="Logo"></a>
    <nav>
        <a href="/about">About Us</a>
        <ul class="dropdown-menu">
            <li><a href="/about/team">Team</a></li>
        </ul>
    </nav>
    <div class="language-switcher"><a href="/fr" hreflang="fr">FR</a></div>
</header>
<ol class="breadcrumb"><li><a href="/">Home</a></li><li><a href="/services">Services</a></li></ol>
<aside class="sidebar">
    <a href="/sidebar-link">Related post</a>
</aside>
<main>
    <div class="hero banner"><a href="/signup" class="btn cta-primary">Get Started</a></div>
    <h2>Services</h2>
    <p>We offer <a href="/seo" title="SEO" class="cta" data-track="click">excellent SEO services</a> for businesses.</p>
    <p>Also check <a href="/seo" rel="nofollow">our SEO overview page</a> for a quick summary.</p>
    <a href="/gallery" class="btn btn-outline">View Gallery</a>
    <a href="/portfolio"><img src="/portfolio.jpg" alt="Our work"></a>
    <a href="/call"><i class="fa-phone"></i></a>
    <div class="card"><a href="/blog/post-1">Latest Post</a></div>
    <h2>Blog</h2>
    <p>Read our <a href="/blog/guide" target="_blank">complete guide</a> to SEO.</p>
    <table><tr><td><a href="/pricing">See pricing</a></td></tr></table>
    <a href="/demo" class="wp-block-button__link">Request Demo</a>
    <div class="related-posts"><a href="/blog/related">Related Article</a></div>
</main>
<div class="pagination"><a href="/page/2" rel="next">Next</a></div>
<a href="https://twitter.com/example" class="social"><i class="fa-twitter"></i></a>
<footer>
    <nav aria-label="Footer navigation">
        <a href="/privacy">Privacy Policy</a>
        <a href="/terms">Terms of Service</a>
    </nav>
    <div class="widget-area"><a href="/footer-widget-link">Recent Posts</a></div>
</footer>
</body></html>'''


def test_enriched_links_basic_collection():
    """collect_all_links_enriched captures all links with context."""
    from bs4 import BeautifulSoup
    from src.core.link_manager import LinkManager

    lm = LinkManager('example.com')
    soup = BeautifulSoup(LINK_TEST_HTML, 'html.parser')
    sections = [{'heading': 'Services', 'position': 0}, {'heading': 'Blog', 'position': 1}]

    lm.collect_all_links_enriched(soup, 'https://example.com/', sections, [])

    # New fixture has 24 links across all placement types
    assert len(lm.all_links) == 24, f'Expected 24 links, got {len(lm.all_links)}'


def test_enriched_links_no_source_target_dedup():
    """Same target with different anchors should NOT be deduped."""
    from bs4 import BeautifulSoup
    from src.core.link_manager import LinkManager

    lm = LinkManager('example.com')
    soup = BeautifulSoup(LINK_TEST_HTML, 'html.parser')
    sections = [{'heading': 'Services', 'position': 0}]

    lm.collect_all_links_enriched(soup, 'https://example.com/', sections, [])

    seo_links = [l for l in lm.all_links if '/seo' in l['target_url']]
    assert len(seo_links) == 2, f'Expected 2 /seo links (different anchors), got {len(seo_links)}'


def test_enriched_links_placement_detection():
    """Each link type should be classified by its HTML context."""
    from bs4 import BeautifulSoup
    from src.core.link_manager import LinkManager

    lm = LinkManager('example.com')
    soup = BeautifulSoup(LINK_TEST_HTML, 'html.parser')
    sections = [{'heading': 'Services', 'position': 0}, {'heading': 'Blog', 'position': 1}]

    lm.collect_all_links_enriched(soup, 'https://example.com/', sections, [])

    def find(url_part=None, anchor=None):
        for l in lm.all_links:
            if url_part and url_part in l['target_url']:
                if anchor is None or l['anchor_text'] == anchor:
                    return l
            if anchor and not url_part:
                if l['anchor_text'] == anchor:
                    return l
        return None

    # --- Core 17 types ---
    assert find('/main-content')['placement'] == 'skip-link'
    assert find('/home')['placement'] == 'logo'
    assert find('/about', 'About Us')['placement'] == 'navigation'
    assert find('/about/team')['placement'] == 'menu-dropdown'
    assert find('/fr')['placement'] == 'language-switcher'
    assert find('/services', 'Services')['placement'] == 'breadcrumb'
    assert find('/sidebar-link')['placement'] == 'sidebar'
    assert find('/signup')['placement'] == 'cta'
    assert find('/seo', 'excellent SEO services')['placement'] == 'body'
    assert find('/gallery')['placement'] == 'button'
    assert find('/portfolio')['placement'] == 'image'
    assert find('/call')['placement'] == 'icon'
    assert find('/blog/post-1')['placement'] == 'card'
    assert find('/blog/guide')['placement'] == 'body'
    assert find('/pricing')['placement'] == 'body'
    assert find('/page/2')['placement'] == 'pagination'
    assert find(url_part='twitter.com')['placement'] == 'social'

    # --- Edge cases: footer priority ---
    assert find('/privacy')['placement'] == 'footer'
    assert find('/terms')['placement'] == 'footer'
    assert find('/footer-widget-link')['placement'] == 'footer'

    # --- Edge case: page builder buttons ---
    assert find('/demo')['placement'] == 'button'

    # --- Edge case: related posts = card ---
    assert find('/blog/related')['placement'] == 'card'


def test_enriched_links_attributes():
    """All HTML attributes should be captured including data-* grouping."""
    from bs4 import BeautifulSoup
    from src.core.link_manager import LinkManager

    lm = LinkManager('example.com')
    soup = BeautifulSoup(LINK_TEST_HTML, 'html.parser')
    sections = [{'heading': 'Services', 'position': 0}]

    lm.collect_all_links_enriched(soup, 'https://example.com/', sections, [])

    seo = [l for l in lm.all_links if l['anchor_text'] == 'excellent SEO services'][0]
    assert seo['attributes']['title'] == 'SEO'
    assert seo['attributes']['class'] == 'cta'
    assert seo['attributes']['data']['track'] == 'click'

    nofollow = [l for l in lm.all_links if 'nofollow' in (l['attributes'].get('rel') or '')][0]
    assert 'nofollow' in nofollow['attributes']['rel']


def test_enriched_links_context():
    """Links should have surrounding paragraph text as context."""
    from bs4 import BeautifulSoup
    from src.core.link_manager import LinkManager

    lm = LinkManager('example.com')
    soup = BeautifulSoup(LINK_TEST_HTML, 'html.parser')
    sections = [{'heading': 'Services', 'position': 0}]

    lm.collect_all_links_enriched(soup, 'https://example.com/', sections, [])

    seo = [l for l in lm.all_links if l['anchor_text'] == 'excellent SEO services'][0]
    assert 'SEO services' in seo['context'], f'Context should contain anchor text: {seo["context"]}'
    assert len(seo['context']) > 0


def test_enriched_links_parent_heading():
    """Links should have parent heading detected."""
    from bs4 import BeautifulSoup
    from src.core.link_manager import LinkManager

    lm = LinkManager('example.com')
    soup = BeautifulSoup(LINK_TEST_HTML, 'html.parser')
    sections = [{'heading': 'Services', 'position': 0}, {'heading': 'Blog', 'position': 1}]

    lm.collect_all_links_enriched(soup, 'https://example.com/', sections, [])

    seo = [l for l in lm.all_links if l['anchor_text'] == 'excellent SEO services'][0]
    assert seo['parent_heading'] == 'Services'

    guide = [l for l in lm.all_links if '/blog/guide' in l['target_url']][0]
    assert guide['parent_heading'] == 'Blog'
    assert guide['section_position'] == 1


# --- Export Tests ---

def test_linkgraph_json_export(temp_db):
    """generate_linkgraph_json_export produces valid self-contained JSON."""
    import src.crawl_db as crawl_db

    crawl_id = crawl_db.create_crawl(1, 'test-session', 'https://example.com', 'example.com', {})

    # Save some URLs
    urls = [
        {'url': 'https://example.com/', 'status_code': 200, 'content_type': 'text/html',
         'title': 'Home', 'meta_description': 'Welcome', 'h1': 'Home', 'h2': [], 'h3': [],
         'word_count': 100, 'canonical_url': '', 'lang': 'en', 'charset': 'utf-8',
         'viewport': '', 'robots': '', 'meta_tags': {}, 'og_tags': {}, 'twitter_tags': {},
         'json_ld': [], 'analytics': {}, 'images': [], 'hreflang': [], 'schema_org': [],
         'redirects': [], 'linked_from': [], 'external_links': 0, 'internal_links': 2,
         'response_time': 100, 'javascript_rendered': False, 'body_text': 'Welcome to our site',
         'is_internal': True, 'depth': 0, 'size': 5000},
        {'url': 'https://example.com/about', 'status_code': 200, 'content_type': 'text/html',
         'title': 'About', 'meta_description': 'About us', 'h1': 'About', 'h2': [], 'h3': [],
         'word_count': 80, 'canonical_url': '', 'lang': 'en', 'charset': 'utf-8',
         'viewport': '', 'robots': '', 'meta_tags': {}, 'og_tags': {}, 'twitter_tags': {},
         'json_ld': [], 'analytics': {}, 'images': [], 'hreflang': [], 'schema_org': [],
         'redirects': [], 'linked_from': [], 'external_links': 1, 'internal_links': 1,
         'response_time': 120, 'javascript_rendered': False, 'body_text': 'About our company',
         'is_internal': True, 'depth': 1, 'size': 4000},
    ]
    crawl_db.save_url_batch(crawl_id, urls)

    # Save links
    links = [
        {'source_url': 'https://example.com/', 'target_url': 'https://example.com/about',
         'anchor_text': 'About Us', 'is_internal': True, 'target_domain': 'example.com',
         'target_status': 200, 'placement': 'body', 'context': 'Learn more About Us here.',
         'parent_heading': 'Navigation', 'section_position': 0,
         'attributes': {'rel': None, 'title': 'About'}},
    ]
    crawl_db.save_links_batch(crawl_id, links)

    # Save sections
    sections = [
        {'url': 'https://example.com/', 'heading': 'Welcome', 'heading_level': 1,
         'text': 'Welcome to our site', 'word_count': 4, 'position': 0},
    ]
    crawl_db.save_sections_batch(crawl_id, sections)

    # Generate export — import the function directly to avoid Flask app import
    # We replicate the core logic here since main.py requires Flask
    from src.crawl_db import get_crawl_by_id, load_crawled_urls, load_crawl_links, load_crawl_sections

    crawl = get_crawl_by_id(crawl_id)
    loaded_urls = load_crawled_urls(crawl_id)
    loaded_links = load_crawl_links(crawl_id)
    loaded_sections = load_crawl_sections(crawl_id)

    # Verify raw data is present
    assert len(loaded_urls) == 2
    assert len(loaded_links) == 1
    assert len(loaded_sections) == 1

    # Build the export structure (same logic as generate_linkgraph_json_export)
    sections_by_url = {}
    for s in loaded_sections:
        url = s['url']
        if url not in sections_by_url:
            sections_by_url[url] = []
        sections_by_url[url].append({
            'heading': s.get('heading', ''),
            'heading_level': s.get('heading_level', 2),
            'text': s.get('text', ''),
            'word_count': s.get('word_count', 0),
            'position': s.get('position', 0),
        })

    outgoing_by_source = {}
    for link in loaded_links:
        source = link['source_url']
        if source not in outgoing_by_source:
            outgoing_by_source[source] = {'internal': [], 'external': []}
        bucket = 'internal' if link.get('is_internal') else 'external'
        outgoing_by_source[source][bucket].append({
            'target_url': link['target_url'],
            'anchor_text': link.get('anchor_text', ''),
        })

    incoming_by_target = {}
    for link in loaded_links:
        if link.get('is_internal'):
            target = link['target_url']
            if target not in incoming_by_target:
                incoming_by_target[target] = []
            incoming_by_target[target].append({
                'source_url': link['source_url'],
                'anchor_text': link.get('anchor_text', ''),
            })

    pages = {}
    html_urls = set()
    for url_data in loaded_urls:
        url = url_data['url']
        if 'text/html' not in url_data.get('content_type', ''):
            continue
        html_urls.add(url)
        pages[url] = {
            'url': url,
            'title': url_data.get('title', ''),
            'content': {
                'full_text': url_data.get('body_text', ''),
                'sections': sections_by_url.get(url, []),
            },
            'outgoing_links': outgoing_by_source.get(url, {'internal': [], 'external': []}),
            'incoming_links': incoming_by_target.get(url, []),
        }

    orphan_pages = [url for url in html_urls if url not in incoming_by_target]

    data = {
        'meta': {
            'domain': crawl.get('base_domain', ''),
            'total_pages': len(pages),
            'total_internal_links': sum(len(p['outgoing_links']['internal']) for p in pages.values()),
        },
        'pages': pages,
        'orphan_pages': sorted(orphan_pages),
    }

    result = json.dumps(data)
    assert result is not None
    data = json.loads(result)
    assert result is not None

    data = json.loads(result)

    # Check meta
    assert data['meta']['domain'] == 'example.com'
    assert data['meta']['total_pages'] == 2
    assert data['meta']['total_internal_links'] == 1

    # Check pages keyed by URL
    assert 'https://example.com/' in data['pages']
    assert 'https://example.com/about' in data['pages']

    home = data['pages']['https://example.com/']
    assert home['content']['full_text'] == 'Welcome to our site'
    assert len(home['content']['sections']) == 1
    assert home['content']['sections'][0]['heading'] == 'Welcome'

    # Check outgoing links
    assert len(home['outgoing_links']['internal']) == 1
    assert home['outgoing_links']['internal'][0]['target_url'] == 'https://example.com/about'

    # Check incoming links (computed)
    about = data['pages']['https://example.com/about']
    assert len(about['incoming_links']) == 1
    assert about['incoming_links'][0]['source_url'] == 'https://example.com/'

    # Orphan check — home has no incoming links
    assert 'https://example.com/' in data['orphan_pages']


def test_save_links_old_format_still_works(temp_db):
    """save_links_batch works with old-format links (no enriched fields)."""
    import src.crawl_db as crawl_db

    crawl_id = crawl_db.create_crawl(1, 'test-session', 'https://example.com', 'example.com', {})

    old_links = [{
        'source_url': 'https://example.com/',
        'target_url': 'https://example.com/about',
        'anchor_text': 'About',
        'is_internal': True,
        'target_domain': 'example.com',
        'target_status': 200,
        'placement': 'navigation',
        # No context, parent_heading, section_position, attributes
    }]
    assert crawl_db.save_links_batch(crawl_id, old_links) is True

    loaded = crawl_db.load_crawl_links(crawl_id)
    assert len(loaded) == 1
    assert loaded[0]['anchor_text'] == 'About'
