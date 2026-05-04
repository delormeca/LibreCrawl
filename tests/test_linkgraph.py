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
    """extract_sections splits content by H2/H3 headings."""
    from src.core.seo_extractor import SEOExtractor
    # Each paragraph must be >30 words to avoid being merged into the previous section
    html = '''<html><body>
    <h1>Page Title</h1>
    <p>Intro paragraph with enough words to pass the thirty word minimum for section extraction testing purposes here and more words to be safe about it and even more padding words.</p>
    <h2>First Section</h2>
    <p>Content under first section with enough words to exceed the thirty word minimum threshold for the merge logic testing purposes here and additional padding to be safe and ensure quality.</p>
    <h3>Subsection A</h3>
    <p>Content under subsection A with enough words to exceed the thirty word minimum threshold for testing purposes and verification and extra padding words to ensure this section stands alone independently.</p>
    <h2>Second Section</h2>
    <p>Content under second section with enough words to be independently meaningful for embedding and vectorization purposes here plus additional padding words to ensure standalone viability of this section and more words to exceed the absolute minimum threshold.</p>
    </body></html>'''
    sections = SEOExtractor.extract_sections(html, title='Page Title')
    assert len(sections) >= 3, f'Expected >= 3 sections, got {len(sections)}'
    # First section should be intro
    assert sections[0]['heading_level'] == 1
    assert sections[0]['position'] == 0
    # Check headings are captured
    headings = [s['heading'] for s in sections]
    assert 'First Section' in headings
    assert 'Second Section' in headings
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
    """Tables should be converted to readable text, not stripped."""
    from src.core.seo_extractor import SEOExtractor
    html = '''<html><body>
    <h2>Data Table</h2>
    <table><tr><th>Name</th><th>Value</th></tr><tr><td>Alpha</td><td>100</td></tr><tr><td>Beta</td><td>200</td></tr></table>
    <p>Additional paragraph content with enough words to make the section meaningful for testing purposes here.</p>
    </body></html>'''
    sections = SEOExtractor.extract_sections(html, title='Test')
    assert len(sections) >= 1
    # Find the Data Table section
    data_section = [s for s in sections if s['heading'] == 'Data Table']
    assert len(data_section) == 1
    assert 'Alpha' in data_section[0]['text']
    assert 'Beta' in data_section[0]['text']


def test_extract_sections_empty_html():
    """Empty HTML returns empty list."""
    from src.core.seo_extractor import SEOExtractor
    assert SEOExtractor.extract_sections('', title='X') == []
    assert SEOExtractor.extract_sections(None, title='X') == []


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
