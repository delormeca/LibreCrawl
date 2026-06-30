"""Crawl mode auto-detection and escalation."""


class CrawlStrategy:
    """Manages smart mode selection: fast (Chromium) vs stealth (CamoFox + proxy)."""

    def __init__(self, strategy='smart', proxy_url=None, fast_concurrency=3, escalation_threshold=3):
        self.strategy = strategy
        self.proxy_url = proxy_url
        self.fast_concurrency = fast_concurrency
        self.escalation_threshold = escalation_threshold

        self.current_mode = 'fast'
        self.consecutive_blocks = 0
        self.stealth_retries = 0
        self.fast_successes = 0
        self.total_pages = 0
        self.challenge_solved = False

    def can_use_stealth(self):
        """Whether CamoFox stealth rendering is available (always True — built-in)."""
        return True

    def can_use_proxy(self):
        """Whether proxy-based stealth is available."""
        return bool(self.proxy_url)

    def should_use_stealth(self):
        """Whether the NEXT page should use stealth rendering."""
        if self.strategy == 'force_stealth':
            return True
        if self.strategy == 'force_fast':
            return False
        return self.current_mode in ('stealth', 'stealth_fast')

    def report_success(self):
        """Page loaded successfully in current mode."""
        self.total_pages += 1
        self.consecutive_blocks = 0
        self.fast_successes += 1

    def report_block(self, block_type='hard_block'):
        """Page was blocked. Returns action to take."""
        self.total_pages += 1
        self.consecutive_blocks += 1
        self.stealth_retries += 1

        if block_type == 'challenge':
            return 'retry_stealth'

        if not self.proxy_url:
            return 'skip'

        if self.consecutive_blocks >= self.escalation_threshold:
            self.current_mode = 'stealth'
            return 'escalated'

        return 'retry_stealth'

    def report_challenge_solved(self):
        """Challenge was solved — cookie is set, safe to increase concurrency."""
        self.challenge_solved = True
        self.current_mode = 'stealth_fast'

    def get_concurrency(self):
        """Current max concurrent pages.
        CamoFox must stay at 1 — concurrent navigations timeout on protected sites."""
        if self.strategy == 'force_fast':
            return self.fast_concurrency
        if self.should_use_stealth():
            return 1
        return self.fast_concurrency

    def get_status(self):
        """Current mode label for UI."""
        if self.strategy == 'force_stealth':
            return 'STEALTH'
        if self.strategy == 'force_fast':
            return 'FAST'
        if self.current_mode in ('stealth', 'stealth_fast'):
            return 'STEALTH'
        if self.fast_successes >= 5:
            return 'FAST'
        return 'SMART'

    def get_stats(self):
        """Full stats for API response."""
        return {
            'mode': self.get_status(),
            'strategy': self.strategy,
            'stealth_retries': self.stealth_retries,
            'fast_successes': self.fast_successes,
            'consecutive_blocks': self.consecutive_blocks,
            'concurrency': self.get_concurrency(),
            'total_pages': self.total_pages,
            'challenge_solved': self.challenge_solved,
        }
