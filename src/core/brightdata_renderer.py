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

            if resp.status_code == 200:
                return resp.text, 200
            else:
                print(f"BrightData error for {url}: HTTP {resp.status_code}")
                return resp.text, resp.status_code

        except http_requests.Timeout:
            print(f"BrightData timeout for {url}")
            return "", 0
        except Exception as e:
            print(f"BrightData error for {url}: {e}")
            return "", 0
