"""
Standalone stealth browser renderer using CamoFox (patched Firefox).
Does not depend on JavaScriptRenderer or its Chromium page pool.
"""
import asyncio
from urllib.parse import urlparse


class CamoFoxRenderer:
    """Renders pages using CamoFox — a Firefox fork with C++-level anti-detection."""

    def __init__(self, proxy_url=None):
        self.proxy = self._parse_proxy(proxy_url) if proxy_url else None

    @staticmethod
    def _parse_proxy(proxy_url):
        """Parse a proxy URL string into CamoFox's proxy dict format."""
        parsed = urlparse(proxy_url)
        proxy = {'server': f'{parsed.scheme}://{parsed.hostname}:{parsed.port}'}
        if parsed.username:
            proxy['username'] = parsed.username
        if parsed.password:
            proxy['password'] = parsed.password
        return proxy

    async def render_page(self, url, wait_time=3, timeout=30):
        """Render a page using CamoFox stealth browser.

        Args:
            url: The URL to render
            wait_time: Seconds to wait after page load for JS to settle
            timeout: Max seconds for page load

        Returns:
            Tuple of (html_content: str, status_code: int)
        """
        from camoufox.async_api import AsyncCamoufox

        launch_opts = {'headless': True}
        if self.proxy:
            launch_opts['proxy'] = self.proxy
            launch_opts['geoip'] = True

        async with AsyncCamoufox(**launch_opts) as browser:
            page = await browser.new_page()
            response = await page.goto(url, timeout=timeout * 1000)
            await page.wait_for_timeout(wait_time * 1000)
            content = await page.content()
            status = response.status if response else 200
            await page.close()
        return content, status
