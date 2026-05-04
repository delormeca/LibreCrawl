import pytest
from unittest.mock import MagicMock, patch
from src.core.sitemap_parser import SitemapParser


def make_parser():
    session = MagicMock()
    return SitemapParser(session, 'example.com', timeout=5)


class TestWpSitemapProbe:
    def test_wp_sitemap_in_probe_list(self):
        """wp-sitemap.xml should be probed during discovery."""
        parser = make_parser()
        fetched = []
        def mock_fetch(url):
            fetched.append(url)
            return 404, b''
        parser._fetch = mock_fetch
        parser._discover_sitemaps_inner('https://example.com')
        assert any('/wp-sitemap.xml' in u for u in fetched)


class TestExtraUrls:
    def test_extra_urls_are_fetched(self):
        """User-provided sitemap URLs should be fetched."""
        parser = make_parser()
        fetched = []
        def mock_fetch(url):
            fetched.append(url)
            return 404, b''
        parser._fetch = mock_fetch
        parser.discover_sitemaps(
            'https://example.com',
            extra_urls=['https://example.com/media/sitemap/custom.xml']
        )
        assert 'https://example.com/media/sitemap/custom.xml' in fetched

    def test_extra_urls_parsed_correctly(self):
        """URLs inside user-provided sitemaps should be returned."""
        parser = make_parser()
        sitemap_xml = b'''<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/page1</loc></url>
            <url><loc>https://example.com/page2</loc></url>
        </urlset>'''
        def mock_fetch(url):
            if 'custom.xml' in url:
                return 200, sitemap_xml
            return 404, b''
        parser._fetch = mock_fetch
        urls = parser.discover_sitemaps(
            'https://example.com',
            extra_urls=['https://example.com/custom.xml']
        )
        assert 'https://example.com/page1' in urls
        assert 'https://example.com/page2' in urls


class TestProbeDedup:
    def test_duplicate_probes_fetched_once(self):
        """If robots.txt lists /sitemap.xml, don't fetch it twice."""
        parser = make_parser()
        fetched = []
        robots_txt = b'Sitemap: https://example.com/sitemap.xml\n'
        def mock_fetch(url):
            fetched.append(url)
            if 'robots.txt' in url:
                return 200, robots_txt
            return 404, b''
        parser._fetch = mock_fetch
        parser._discover_sitemaps_inner('https://example.com')
        sitemap_fetches = [u for u in fetched if u == 'https://example.com/sitemap.xml']
        assert len(sitemap_fetches) == 1

    def test_no_extra_urls_is_backward_compatible(self):
        """Calling without extra_urls works identically to before."""
        parser = make_parser()
        fetched = []
        def mock_fetch(url):
            fetched.append(url)
            return 404, b''
        parser._fetch = mock_fetch
        parser.discover_sitemaps('https://example.com')
        assert any('/sitemap.xml' in u for u in fetched)


class TestCrawlerSitemapSeeding:
    def _make_crawler(self):
        """Create a WebCrawler with minimal init for testing."""
        from src.crawler import WebCrawler
        crawler = WebCrawler()
        crawler.base_domain = 'example.com'
        crawler._initialize_components()
        return crawler

    def test_set_user_sitemap_urls(self):
        """Crawler should accept and store user sitemap URLs."""
        from src.crawler import WebCrawler
        crawler = WebCrawler()
        crawler.set_user_sitemap_urls([
            'https://example.com/custom-sitemap.xml'
        ])
        assert crawler._user_sitemap_urls == ['https://example.com/custom-sitemap.xml']

    def test_sitemap_url_count_in_status(self):
        """get_status should include sitemap_url_count."""
        crawler = self._make_crawler()
        crawler.sitemap_url_count = 42
        status = crawler.get_status()
        assert status['stats']['sitemap_url_count'] == 42

    def test_sitemap_url_count_defaults_to_zero(self):
        """sitemap_url_count should be 0 when no sitemaps found."""
        crawler = self._make_crawler()
        status = crawler.get_status()
        assert status['stats']['sitemap_url_count'] == 0
