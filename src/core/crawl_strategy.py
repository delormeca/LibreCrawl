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

    def can_use_stealth(self):
        """Whether stealth fallback is available (proxy configured)."""
        return bool(self.proxy_url)

    def should_use_stealth(self):
        """Whether the NEXT page should use stealth rendering."""
        if self.strategy == 'force_stealth':
            return True
        if self.strategy == 'force_fast':
            return False
        return self.current_mode == 'stealth'

    def report_success(self):
        """Page loaded successfully in current mode."""
        self.total_pages += 1
        self.consecutive_blocks = 0
        self.fast_successes += 1

    def report_block(self):
        """Page was blocked (403/503/empty). Returns action to take."""
        self.total_pages += 1

        if not self.can_use_stealth():
            return 'skip'

        self.consecutive_blocks += 1
        self.stealth_retries += 1

        if self.consecutive_blocks >= self.escalation_threshold:
            self.current_mode = 'stealth'
            return 'escalated'

        return 'retry_stealth'

    def get_concurrency(self):
        """Current max concurrent pages."""
        if self.should_use_stealth():
            return 1
        return self.fast_concurrency

    def get_status(self):
        """Current mode label for UI."""
        if self.strategy == 'force_stealth':
            return 'STEALTH'
        if self.strategy == 'force_fast':
            return 'FAST'
        if self.current_mode == 'stealth':
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
        }
