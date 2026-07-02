"""
Standalone stealth browser renderer using CamoFox (patched Firefox).
Does not depend on JavaScriptRenderer or its Chromium page pool.

Two modes:
- Persistent: call start()/stop() around a batch. One browser, many pages.
- One-shot: call render_page() without start(). Launches+kills per page.
  Used by the sync fallback path (asyncio.run per call).
"""
import asyncio
from urllib.parse import urlparse


class CamoFoxRenderer:
    """Renders pages using CamoFox — a Firefox fork with C++-level anti-detection."""

    def __init__(self, proxy_url=None):
        self.proxy = self._parse_proxy(proxy_url) if proxy_url else None
        self._browser = None
        self._camoufox = None

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

    def _launch_opts(self):
        opts = {'headless': True}
        if self.proxy:
            opts['proxy'] = self.proxy
            opts['geoip'] = True
        return opts

    async def start(self):
        """Launch a persistent browser for batch crawling."""
        if self._browser is None:
            from camoufox.async_api import AsyncCamoufox
            self._camoufox = AsyncCamoufox(**self._launch_opts())
            self._browser = await self._camoufox.__aenter__()
            print("CamoFox browser launched (persistent)")

    async def stop(self):
        """Shut down the persistent browser."""
        if self._camoufox:
            try:
                await self._camoufox.__aexit__(None, None, None)
                print("CamoFox browser closed")
            except Exception as e:
                print(f"Error closing CamoFox: {e}")
            self._browser = None
            self._camoufox = None

    async def render_page(self, url, wait_time=3, timeout=30):
        """Render a page. Uses persistent browser if started, otherwise one-shot."""
        if self._browser:
            return await self._render_with_browser(self._browser, url, wait_time, timeout)

        # One-shot: launch and kill per page (for sync fallback path)
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(**self._launch_opts()) as browser:
            return await self._render_with_browser(browser, url, wait_time, timeout)

    # Resource types that waste bandwidth without adding SEO value
    _BLOCKED_TYPES = {'image', 'media', 'font'}

    # Third-party domains that waste bandwidth (analytics, tracking, ads, CDN fonts)
    _BLOCKED_DOMAINS = {
        'googletagmanager.com', 'google-analytics.com', 'googlesyndication.com',
        'googleadservices.com', 'doubleclick.net',
        'facebook.net', 'facebook.com', 'fbcdn.net',
        'connect.facebook.net',
        'cookielaw.org', 'onetrust.com',
        'hotjar.com', 'clarity.ms', 'mouseflow.com',
        'pinimg.com', 'pinterest.com',
        'techlab-cdn.com', 'adsrvr.org', 'demdex.net',
        'fonts.googleapis.com', 'fonts.gstatic.com',
    }

    # Domains that must NEVER be blocked (Cloudflare challenge scripts)
    _ALLOWED_DOMAINS = {'cloudflare.com', 'cloudflareinsights.com'}

    @staticmethod
    async def _block_heavy_resources(route):
        url = route.request.url
        # Block by resource type (images, media, fonts)
        if route.request.resource_type in CamoFoxRenderer._BLOCKED_TYPES:
            await route.abort()
            return
        # Block third-party tracking/analytics domains (but never Cloudflare)
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).hostname or ''
            if any(domain.endswith(d) for d in CamoFoxRenderer._ALLOWED_DOMAINS):
                await route.continue_()
                return
            if any(domain.endswith(d) for d in CamoFoxRenderer._BLOCKED_DOMAINS):
                await route.abort()
                return
        except Exception:
            pass
        await route.continue_()

    # Common cookie consent accept button selectors
    _COOKIE_ACCEPT_SELECTORS = [
        '[id*="accept" i]', '[class*="accept" i]',
        '[id*="agree" i]', '[class*="agree" i]',
        '[id*="consent" i] button', '[class*="consent" i] button',
        '[id*="cookie" i] button', '[class*="cookie" i] button',
        '.cc-accept', '.cc-allow', '#onetrust-accept-btn-handler',
    ]

    # Challenge page title signatures — used to detect and wait for resolution
    _CHALLENGE_TITLES = [
        'just a moment', 'vercel security checkpoint',
        'attention required', 'checking your browser',
    ]

    @staticmethod
    async def _dismiss_cookie_banner(page):
        """Try to click cookie accept buttons to dismiss banners."""
        for selector in CamoFoxRenderer._COOKIE_ACCEPT_SELECTORS:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    await btn.click(timeout=1000)
                    return
            except Exception:
                continue

    @staticmethod
    async def _render_with_browser(browser, url, wait_time, timeout, retries=2):
        last_error = None
        for attempt in range(retries + 1):
            page = await browser.new_page()
            try:
                # No route interception on first load — page.route() triggers
                # Cloudflare/Vercel bot detection on datacenter IPs
                response = await page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)

                # Check if we landed on a challenge page
                title = await page.title()
                is_challenge = any(sig in title.lower() for sig in CamoFoxRenderer._CHALLENGE_TITLES)

                if is_challenge:
                    print(f"CamoFox: challenge detected for {url} (title: {title})")
                    # Wait for challenge to solve — it triggers reload/redirect
                    try:
                        await page.wait_for_event('load', timeout=20000)
                        # After reload, wait for content to render
                        await page.wait_for_timeout(2000)
                    except Exception:
                        print(f"CamoFox: challenge wait timed out for {url}")

                    # Re-check: did challenge resolve?
                    new_title = await page.title()
                    still_challenge = any(sig in new_title.lower()
                                          for sig in CamoFoxRenderer._CHALLENGE_TITLES)
                    if still_challenge:
                        print(f"CamoFox: challenge NOT resolved for {url} (still: {new_title})")
                        content = await page.content()
                        status = response.status if response else 403
                        return content, status
                    else:
                        print(f"CamoFox: challenge SOLVED for {url} (now: {new_title})")
                else:
                    # No challenge — normal wait for JS rendering
                    await page.wait_for_timeout(wait_time * 1000)

                await CamoFoxRenderer._dismiss_cookie_banner(page)
                content = await page.content()
                status = response.status if response else 200
                return content, status
            except Exception as e:
                last_error = e
                if attempt < retries:
                    print(f"CamoFox retry {attempt + 1}/{retries} for {url}: {e}")
                else:
                    print(f"CamoFox failed after {retries + 1} attempts for {url}: {e}")
            finally:
                await page.close()
        raise last_error
