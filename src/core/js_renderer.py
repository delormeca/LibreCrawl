"""JavaScript rendering handler using Playwright"""
import asyncio
import re
import threading
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from urllib.parse import urlparse


class JavaScriptRenderer:
    """Handles JavaScript rendering for dynamic content using Playwright"""

    def __init__(self, config):
        self.config = config
        self.playwright = None
        self.browser = None
        self.page_pool = []
        self.pool_lock = threading.Lock()

    async def initialize(self):
        """Initialize Playwright browser and page pool"""
        try:
            print("Starting Playwright browser...")
            self.playwright = await async_playwright().start()

            # Choose browser based on configuration
            browser_type = self.config.get('js_browser', 'chromium').lower()
            headless = self.config.get('js_headless', True)

            if browser_type == 'firefox':
                self.browser = await self.playwright.firefox.launch(headless=headless)
            elif browser_type == 'webkit':
                self.browser = await self.playwright.webkit.launch(headless=headless)
            else:  # Default to chromium
                args = ['--no-sandbox', '--disable-dev-shm-usage'] if headless else []
                self.browser = await self.playwright.chromium.launch(headless=headless, args=args)

            # Create page pool
            max_pages = self.config.get('js_max_concurrent_pages', 3)
            for i in range(max_pages):
                context = await self.browser.new_context(
                    user_agent=self.config.get('js_user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
                    viewport={
                        'width': self.config.get('js_viewport_width', 1920),
                        'height': self.config.get('js_viewport_height', 1080)
                    }
                )
                page = await context.new_page()
                page.set_default_timeout(self.config.get('js_timeout', 30) * 1000)
                self.page_pool.append(page)

            print(f"JavaScript rendering initialized with {len(self.page_pool)} browser pages")

        except Exception as e:
            print(f"Failed to initialize JavaScript rendering: {e}")
            await self.cleanup()
            raise

    async def cleanup(self):
        """Clean up Playwright browser and resources"""
        try:
            if self.page_pool:
                for page in self.page_pool:
                    try:
                        await page.context.close()
                    except:
                        pass
                self.page_pool.clear()

            if self.browser:
                await self.browser.close()
                self.browser = None

            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

            print("JavaScript rendering resources cleaned up")

        except Exception as e:
            print(f"Error during JavaScript cleanup: {e}")

    async def get_page(self):
        """Get an available page from the pool"""
        with self.pool_lock:
            if self.page_pool:
                return self.page_pool.pop()
        return None

    async def return_page(self, page):
        """Return a page to the pool"""
        with self.pool_lock:
            self.page_pool.append(page)

    async def set_auth_cookies(self, cookies):
        """Add authentication cookies to all page contexts."""
        for page in self.page_pool:
            await page.context.add_cookies(cookies)
        print(f"Auth cookies applied to {len(self.page_pool)} Chromium contexts")

    _SCROLL_SCRIPT = """async () => {
        let lastHeight = 0;
        for (let i = 0; i < 10; i++) {
            window.scrollTo(0, document.body.scrollHeight);
            await new Promise(r => setTimeout(r, 500));
            if (document.body.scrollHeight === lastHeight) break;
            lastHeight = document.body.scrollHeight;
        }
    }"""

    # Asset extensions to exclude from JS URL discovery
    _ASSET_EXTENSIONS = ('.js', '.css', '.png', '.jpg', '.jpeg', '.gif',
                         '.svg', '.woff', '.woff2', '.ttf', '.ico', '.map')

    async def render_page(self, url, base_domain=None):
        """
        Render a page with JavaScript and return the HTML content.

        When base_domain is provided, also discovers URLs via:
        - Playwright request interception (fetch/XHR)
        - history.pushState / replaceState hooks
        - Regex extraction from inline scripts

        Returns:
            tuple: (html_content, status_code, error_message, js_discovered_urls, redirect_chain)
        """
        page = None
        request_handler = None
        response_handler = None

        try:
            page = await self.get_page()
            if not page:
                return None, 0, "No JavaScript page available", [], []

            intercepted_urls = set()
            redirect_chain = []

            if base_domain:
                def request_handler(request):
                    try:
                        req_url = request.url
                        if base_domain in req_url and request.resource_type in ('document', 'fetch', 'xhr'):
                            intercepted_urls.add(req_url)
                    except Exception:
                        pass

                page.on('request', request_handler)

            # Capture redirect responses (3xx) for the main document navigation
            def response_handler(response):
                try:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get('location', '')
                        if location:
                            redirect_chain.append({
                                'from': response.url,
                                'to': location,
                                'status': response.status
                            })
                except Exception:
                    pass

            page.on('response', response_handler)

            # Navigate to the page
            try:
                response = await page.goto(
                    url,
                    wait_until='domcontentloaded',
                    timeout=self.config.get('js_timeout', 30) * 1000
                )

                if base_domain:
                    # Install pushState/replaceState hooks to capture SPA navigation
                    await page.evaluate("""() => {
                        window.__jsDiscoveredUrls = [];
                        if (!window.__pushStateHooked) {
                            window.__pushStateHooked = true;
                            const origPush = history.pushState;
                            const origReplace = history.replaceState;
                            history.pushState = function() {
                                origPush.apply(this, arguments);
                                if (arguments[2]) window.__jsDiscoveredUrls.push(arguments[2]);
                            };
                            history.replaceState = function() {
                                origReplace.apply(this, arguments);
                                if (arguments[2]) window.__jsDiscoveredUrls.push(arguments[2]);
                            };
                        }
                    }""")

                # Wait for JavaScript to render
                await asyncio.sleep(self.config.get('js_wait_time', 3))

                # Scroll to bottom to trigger lazy-loaded content
                if self.config.get('enable_scroll_before_extract', False):
                    try:
                        await page.evaluate(self._SCROLL_SCRIPT)
                    except Exception:
                        pass

                # Get the rendered HTML content
                html_content = await page.content()
                status_code = response.status if response else 200

                js_discovered_urls = []
                if base_domain:
                    # Collect pushState/replaceState URLs
                    try:
                        push_urls = await page.evaluate('window.__jsDiscoveredUrls || []')
                        for u in push_urls:
                            if u.startswith('/'):
                                try:
                                    parsed = urlparse(url)
                                except ValueError:
                                    continue
                                u = f"{parsed.scheme}://{base_domain}{u}"
                            if base_domain in u:
                                intercepted_urls.add(u)
                    except Exception:
                        pass

                    # Extract URLs from page content via regex (catches hardcoded URLs in scripts)
                    for match in re.finditer(r'https?://[^\s"\'<>\)\}]+', html_content):
                        found_url = match.group().rstrip('.,;:)')
                        if base_domain in found_url:
                            if not found_url.lower().endswith(self._ASSET_EXTENSIONS):
                                intercepted_urls.add(found_url)

                    intercepted_urls.discard(url)
                    js_discovered_urls = list(intercepted_urls)

                return html_content, status_code, None, js_discovered_urls, redirect_chain

            except PlaywrightTimeoutError:
                return None, 0, "JavaScript rendering timeout", [], []
            except Exception as e:
                return None, 0, f"Navigation error: {str(e)}", [], []

        except Exception as e:
            return None, 0, f"JavaScript rendering error: {str(e)}", [], []

        finally:
            if page:
                if request_handler:
                    page.remove_listener('request', request_handler)
                page.remove_listener('response', response_handler)
                await self.return_page(page)

    # Common pagination button selectors and text patterns
    _PAGINATION_SELECTORS = [
        '[aria-label*="next" i]', '[aria-label*="suivant" i]',
        'a[rel="next"]', 'link[rel="next"]',
        '.pagination a', '.pager a',
        '.next-page', '.load-more', '.show-more',
    ]
    _PAGINATION_TEXT = [
        'next', 'load more', 'show more', 'suivant', 'page suivante',
        '›', '»', '→',
    ]

    async def discover_pagination(self, page, base_domain, max_pages=50):
        """
        Detect and click through pagination on a rendered page.
        Returns list of URLs discovered from paginated content.
        """
        discovered_urls = set()
        pages_clicked = 0

        for _ in range(max_pages):
            # Try to find a "next" button
            next_btn = None

            # Check selectors first
            for selector in self._PAGINATION_SELECTORS:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=500):
                        next_btn = el
                        break
                except Exception:
                    continue

            # Fall back to text matching
            if not next_btn:
                for text in self._PAGINATION_TEXT:
                    try:
                        el = page.get_by_text(text, exact=True).first
                        if await el.is_visible(timeout=500):
                            tag = await el.evaluate('el => el.tagName.toLowerCase()')
                            if tag in ('a', 'button'):
                                next_btn = el
                                break
                    except Exception:
                        continue

            if not next_btn:
                break

            # Record DOM height before click
            old_height = await page.evaluate('document.body.scrollHeight')

            try:
                await next_btn.click(timeout=5000)
                # Wait for content change
                await page.wait_for_timeout(2000)
            except Exception:
                break

            # Check if DOM actually changed
            new_height = await page.evaluate('document.body.scrollHeight')
            new_url = page.url

            # Extract new URLs from updated page
            hrefs = await page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
            }''')
            for href in hrefs:
                if base_domain in href:
                    if not href.lower().endswith(self._ASSET_EXTENSIONS):
                        discovered_urls.add(href)

            # Also capture if URL changed (SPA pagination)
            if base_domain in new_url:
                discovered_urls.add(new_url)

            pages_clicked += 1

            # If nothing changed, pagination is done
            if new_height == old_height and new_url == page.url:
                break

        if pages_clicked:
            print(f"Pagination: clicked through {pages_clicked} pages, found {len(discovered_urls)} URLs")
        return list(discovered_urls)

    def should_use_javascript(self, url):
        """Determine if a URL should use JavaScript rendering"""
        parsed = urlparse(url)
        path = parsed.path.lower()

        # Skip if it's clearly a non-HTML resource
        if path.endswith(('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.css', '.js', '.xml', '.txt', '.zip')):
            return False

        return True
