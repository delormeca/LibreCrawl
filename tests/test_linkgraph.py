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
