"""Token bucket rate limiter for smooth request distribution with adaptive backoff"""
import time
import threading


class RateLimiter:
    """
    Token bucket rate limiter that ensures smooth, steady request distribution.
    Prevents bursting by spacing out requests evenly over time.

    Adaptive backoff: consecutive blocked responses (403/429/503) trigger
    progressive slowdown. Resets on successful 200.
    """

    def __init__(self, requests_per_second=1.0):
        self.requests_per_second = max(0.01, requests_per_second)
        self.min_interval = 1.0 / self.requests_per_second if self.requests_per_second > 0 else 0
        self.base_interval = self.min_interval  # remember original for reset
        self.last_request_time = 0
        self.lock = threading.Lock()

        # Adaptive backoff state
        self.consecutive_blocks = 0
        self.is_paused = False
        self.backoff_level = 0  # 0=normal, 1=doubled, 2=quadrupled, 3=paused

    def acquire(self):
        """
        Acquire permission to make a request.
        Blocks until enough time has passed since the last request.
        """
        with self.lock:
            now = time.time()
            time_since_last = now - self.last_request_time

            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                time.sleep(sleep_time)
                self.last_request_time = time.time()
            else:
                self.last_request_time = now

    def update_rate(self, requests_per_second):
        """Update the rate limit dynamically"""
        with self.lock:
            self.requests_per_second = max(0.01, requests_per_second)
            self.min_interval = 1.0 / self.requests_per_second if self.requests_per_second > 0 else 0
            self.base_interval = self.min_interval

    def report_block(self):
        """Report a blocked response (403/429/503). Returns action taken."""
        with self.lock:
            self.consecutive_blocks += 1

            if self.consecutive_blocks >= 10:
                self.backoff_level = 3
                self.min_interval = max(self.base_interval * 4, 4.0)
                # Pause for 30s then resume at reduced speed
                self.lock.release()
                time.sleep(30)
                self.lock.acquire()
                return 'paused_30s'
            elif self.consecutive_blocks >= 5:
                self.backoff_level = 2
                self.min_interval = max(self.base_interval * 4, 4.0)
                return 'quadrupled'
            elif self.consecutive_blocks >= 3:
                self.backoff_level = 1
                self.min_interval = max(self.base_interval * 2, 2.0)
                return 'doubled'
            return 'watching'

    def report_success(self):
        """Report a successful response. Resets backoff."""
        with self.lock:
            if self.consecutive_blocks > 0:
                self.consecutive_blocks = 0
                self.backoff_level = 0
                self.min_interval = self.base_interval

    def get_backoff_stats(self):
        """Return current backoff state for status display."""
        with self.lock:
            delay = self.min_interval
            return {
                'consecutive_blocks': self.consecutive_blocks,
                'backoff_level': self.backoff_level,
                'current_delay': round(delay, 2),
                'base_delay': round(self.base_interval, 2),
            }
