import sqlite3
import os
import sys
import json
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_page_claims_table_exists():
    """page_claims table should be created by init_crawl_tables"""
    from src.crawl_db import init_crawl_tables, DB_FILE
    import src.crawl_db as db_mod
    original_db = db_mod.DB_FILE
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_mod.DB_FILE = f.name
    try:
        init_crawl_tables()
        conn = sqlite3.connect(db_mod.DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='page_claims'")
        assert cursor.fetchone() is not None, "page_claims table should exist"
        cursor.execute("PRAGMA table_info(page_claims)")
        cols = {row[1] for row in cursor.fetchall()}
        assert {'id', 'crawl_id', 'url', 'claim', 'source_text', 'created_at'} <= cols
        conn.close()
    finally:
        db_mod.DB_FILE = original_db
        os.unlink(f.name)


def _setup_test_db():
    """Helper: create temp DB and init tables, return cleanup function."""
    import src.crawl_db as db_mod
    original_db = db_mod.DB_FILE
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    db_mod.DB_FILE = f.name
    f.close()
    db_mod.init_crawl_tables()
    with db_mod.get_db() as conn:
        conn.execute(
            "INSERT INTO crawls (id, session_id, base_url, base_domain, status) VALUES (1, 'test-session', 'https://example.com', 'example.com', 'completed')"
        )
    def cleanup():
        db_mod.DB_FILE = original_db
        os.unlink(f.name)
    return cleanup


def test_save_and_load_claims():
    from src.crawl_db import save_claims_batch, load_claims, count_claims
    cleanup = _setup_test_db()
    try:
        claims = [
            {'url': '/page-a', 'claim': 'Free shipping above $50', 'source_text': 'Enjoy free shipping on orders over $50.'},
            {'url': '/page-a', 'claim': 'Open 24/7', 'source_text': 'We are open 24 hours a day, 7 days a week.'},
            {'url': '/page-b', 'claim': 'Founded in 1990', 'source_text': 'Our company was founded in 1990.'},
        ]
        save_claims_batch(1, claims)
        loaded = load_claims(1)
        assert len(loaded) == 3
        assert count_claims(1) == 3
    finally:
        cleanup()


def test_delete_claims():
    from src.crawl_db import save_claims_batch, delete_claims, count_claims
    cleanup = _setup_test_db()
    try:
        claims = [
            {'url': '/page-a', 'claim': 'Claim 1', 'source_text': 'Source 1'},
            {'url': '/page-b', 'claim': 'Claim 2', 'source_text': 'Source 2'},
        ]
        save_claims_batch(1, claims)
        assert count_claims(1) == 2
        delete_claims(1)
        assert count_claims(1) == 0
    finally:
        cleanup()


def test_claims_stats():
    from src.crawl_db import update_claims_stats, get_claims_stats
    cleanup = _setup_test_db()
    try:
        stats = {'status': 'completed', 'total_pages': 10, 'processed': 10, 'total_claims': 42}
        update_claims_stats(1, stats)
        loaded = get_claims_stats(1)
        assert loaded['status'] == 'completed'
        assert loaded['total_claims'] == 42
        assert get_claims_stats(999) is None
    finally:
        cleanup()


def test_filter_eligible_pages():
    from src.core.claim_extractor import filter_eligible_pages

    pages = [
        {'url': 'https://example.com/', 'status_code': 200, 'word_count': 500, 'body_text': 'Some content here.'},
        {'url': 'https://example.com/page-2/', 'status_code': 200, 'word_count': 50, 'body_text': 'Short.'},
        {'url': 'https://example.com/error', 'status_code': 404, 'word_count': 500, 'body_text': 'Not found page.'},
        {'url': 'https://example.com/page/3/', 'status_code': 200, 'word_count': 500, 'body_text': 'Pagination page.'},
        {'url': 'https://example.com/about', 'status_code': 200, 'word_count': 300, 'body_text': ''},
        {'url': 'https://example.com/good-page', 'status_code': 200, 'word_count': 200, 'body_text': 'Lots of good content here.'},
    ]

    eligible = filter_eligible_pages(pages)
    urls = [p['url'] for p in eligible]

    assert 'https://example.com/' in urls
    assert 'https://example.com/good-page' in urls
    assert len(eligible) == 2


def test_estimate_cost():
    from src.core.claim_extractor import estimate_cost

    pages = [
        {'word_count': 500},
        {'word_count': 1000},
        {'word_count': 300},
    ]

    result = estimate_cost(pages)

    assert result['eligible_pages'] == 3
    assert result['estimated_input_tokens'] > 0
    assert result['estimated_cost'] > 0
    assert result['model'] == 'gpt-4o-mini'
