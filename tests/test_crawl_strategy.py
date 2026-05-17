import pytest
from src.core.crawl_strategy import CrawlStrategy


class TestCrawlStrategy:
    def test_smart_starts_in_fast_mode(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        assert s.should_use_stealth() is False
        assert s.get_concurrency() == 3
        assert s.get_status() == 'SMART'

    def test_force_stealth_always_stealth(self):
        s = CrawlStrategy(strategy='force_stealth', proxy_url='http://proxy:8080')
        assert s.should_use_stealth() is True
        assert s.get_concurrency() == 1
        assert s.get_status() == 'STEALTH'

    def test_force_fast_never_stealth(self):
        s = CrawlStrategy(strategy='force_fast')
        assert s.should_use_stealth() is False
        assert s.get_concurrency() == 3
        assert s.get_status() == 'FAST'

    def test_smart_escalates_after_3_consecutive_blocks(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        s.report_block()
        s.report_block()
        result = s.report_block()
        assert result == 'escalated'
        assert s.should_use_stealth() is True
        assert s.get_concurrency() == 1
        assert s.get_status() == 'STEALTH'

    def test_success_resets_consecutive_blocks(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        s.report_block()
        s.report_block()
        s.report_success()  # resets
        s.report_block()
        assert s.should_use_stealth() is False

    def test_smart_confirms_fast_after_5_successes(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        for _ in range(5):
            s.report_success()
        assert s.get_status() == 'FAST'

    def test_no_proxy_means_no_stealth_fallback(self):
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        assert s.can_use_stealth() is False

    def test_block_without_proxy_skips(self):
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        result = s.report_block()
        assert result == 'skip'
        assert s.should_use_stealth() is False

    def test_get_stats(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        s.report_success()
        s.report_block()
        stats = s.get_stats()
        assert stats['mode'] == 'SMART'
        assert stats['stealth_retries'] == 1
        assert stats['fast_successes'] == 1
        assert stats['consecutive_blocks'] == 1
