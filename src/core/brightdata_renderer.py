"""
Bright Data Web Unlocker renderer.
Cloud-based JS rendering — no local browser required.
Handles Cloudflare/bot-detection via Bright Data's proxy infrastructure.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import requests as http_requests


class BrightDataRenderer:
    """Renders pages via Bright Data Web Unlocker API with full JS execution."""

    def __init__(self, api_key: str, zone: str = "web_unlocker1"):
        self.api_key = api_key
        self.zone = zone
        self.endpoint = "https://api.brightdata.com/request"
        self._executor = ThreadPoolExecutor(max_workers=10)
        self.pages_rendered = 0
        self.cost_per_page = 0.0015  # $1.50 per 1000 pages

    async def start(self):
        """No-op. No browser to launch."""
        print(f"BrightData renderer ready (zone: {self.zone})")

    async def stop(self):
        """No-op. No browser to close."""
        self._executor.shutdown(wait=False)

    async def render_page(self, url: str, wait_time=3, timeout=30):
        """
        Render a page via Bright Data Web Unlocker.

        Returns:
            tuple: (html_content, status_code) — same contract as CamoFoxRenderer
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._sync_render, url, timeout)

    def _sync_render(self, url, timeout=30):
        try:
            resp = http_requests.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                },
                json={
                    "zone": self.zone,
                    "url": url,
                    "format": "raw",
                    "render": True
                },
                timeout=max(timeout, 60) + 15
            )

            # Bright Data returns the original page status code directly.
            # When the API itself fails (auth, quota), status is 4xx/5xx from BD.
            # When it succeeds, the status IS the target page's status (200, 404, etc.)
            status_code = resp.status_code
            content = resp.text

            # BD sometimes returns 200 with empty or near-empty body
            # (challenge solved but page didn't render). Treat as timeout.
            if status_code == 200 and len(content.strip()) < 100:
                print(f"BrightData empty response for {url} ({len(content)} chars)")
                return "", 0

            if status_code == 200:
                self.pages_rendered += 1
            return content, status_code

        except http_requests.Timeout:
            print(f"BrightData timeout for {url}")
            return "", 0
        except Exception as e:
            print(f"BrightData error for {url}: {e}")
            return "", 0

    def fetch_url(self, url, timeout=30):
        """
        Fetch a URL through Bright Data without JS rendering.
        Useful for sitemaps and robots.txt on protected sites.
        Returns (content_bytes, status_code).
        """
        try:
            resp = http_requests.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                },
                json={
                    "zone": self.zone,
                    "url": url,
                    "format": "raw",
                    "render": False
                },
                timeout=timeout + 15
            )
            content = resp.content
            return content, resp.status_code
        except http_requests.Timeout:
            print(f"BrightData fetch timeout for {url}")
            return b"", 0
        except Exception as e:
            print(f"BrightData fetch error for {url}: {e}")
            return b"", 0

    def get_balance(self):
        """Query Bright Data account balance."""
        try:
            resp = http_requests.get(
                "https://api.brightdata.com/zone/cost",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"zone": self.zone},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"balance": data.get("balance", 0), "currency": data.get("currency", "USD")}
        except Exception as e:
            print(f"BrightData balance check failed: {e}")
        return None

    def get_cost_stats(self):
        """Return current crawl cost statistics."""
        return {
            "pages_rendered": self.pages_rendered,
            "estimated_cost": round(self.pages_rendered * self.cost_per_page, 4),
            "cost_per_page": self.cost_per_page
        }
