"""
Standalone stealth browser renderer using CamoFox (patched Firefox).
Does not depend on JavaScriptRenderer or its Chromium page pool.
"""
import asyncio


class CamoFoxRenderer:
    """Renders pages using CamoFox — a Firefox fork with C++-level anti-detection."""

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

        async with AsyncCamoufox(headless=True) as browser:
            page = await browser.new_page()
            response = await page.goto(url, timeout=timeout * 1000)
            await page.wait_for_timeout(wait_time * 1000)
            content = await page.content()
            status = response.status if response else 200
            await page.close()
        return content, status
