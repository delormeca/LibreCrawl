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

    def test_no_proxy_means_no_proxy_fallback(self):
        """CamoFox always available; proxy is separate check."""
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        assert s.can_use_stealth() is True
        assert s.can_use_proxy() is False

    def test_block_without_proxy_skips(self):
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        result = s.report_block()
        assert result == 'skip'
        assert s.should_use_stealth() is False

    def test_can_use_stealth_always_true(self):
        """CamoFox is built-in — stealth always available."""
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        assert s.can_use_stealth() is True

    def test_can_use_proxy_false_without_proxy(self):
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        assert s.can_use_proxy() is False

    def test_can_use_proxy_true_with_proxy(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        assert s.can_use_proxy() is True

    def test_challenge_block_retries_without_proxy(self):
        """Challenge pages can be retried with CamoFox even without proxy."""
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        result = s.report_block(block_type='challenge')
        assert result == 'retry_stealth'

    def test_hard_block_skips_without_proxy(self):
        """Hard WAF blocks need proxy — skip if none."""
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        result = s.report_block(block_type='hard_block')
        assert result == 'skip'

    def test_hard_block_default_param_is_hard_block(self):
        """Default block_type is hard_block for backward compat."""
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        result = s.report_block()
        assert result == 'skip'

    def test_challenge_solved_increases_concurrency(self):
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        assert s.get_concurrency() == 3
        s.current_mode = 'stealth'
        assert s.get_concurrency() == 1
        s.report_challenge_solved()
        assert s.challenge_solved is True
        assert s.get_concurrency() == 3

    def test_stealth_fast_mode_uses_stealth_renderer(self):
        """After challenge solved, should_use_stealth() still returns True."""
        s = CrawlStrategy(strategy='smart', proxy_url=None)
        s.report_challenge_solved()
        assert s.should_use_stealth() is True
        assert s.get_status() == 'STEALTH'

    def test_get_stats(self):
        s = CrawlStrategy(strategy='smart', proxy_url='http://proxy:8080')
        s.report_success()
        s.report_block()
        stats = s.get_stats()
        assert stats['mode'] == 'SMART'
        assert stats['stealth_retries'] == 1
        assert stats['fast_successes'] == 1
        assert stats['consecutive_blocks'] == 1
