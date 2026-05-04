"""Sitemap discovery and parsing"""
import gzip
import xml.etree.ElementTree as ET
from urllib.parse import urlparse


class SitemapParser:
    """Discovers and parses sitemap.xml files"""

    def __init__(self, session, base_domain, timeout=10, stealth_fetcher=None):
        self.session = session
        self.base_domain = base_domain
        self.timeout = timeout
        self.stealth_fetcher = stealth_fetcher

    def discover_sitemaps(self, base_url):
        """
        Discover and parse sitemap.xml files.
        If stealth_fetcher is set, wraps all fetches in a single browser session.

        Returns:
            list: List of URLs found in sitemaps
        """
        if self.stealth_fetcher:
            import asyncio
            return asyncio.run(self._discover_sitemaps_stealth(base_url))
        return self._discover_sitemaps_inner(base_url)

    def _discover_sitemaps_inner(self, base_url):
        parsed_base = urlparse(base_url)
        base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

        sitemap_urls = [
            f"{base_domain}/sitemap.xml",
            f"{base_domain}/sitemap_index.xml",
            f"{base_domain}/sitemaps.xml",
            f"{base_domain}/sitemap/sitemap.xml"
        ]

        robots_sitemaps = self._get_sitemaps_from_robots(base_domain)
        sitemap_urls.extend(robots_sitemaps)

        print(f"Discovering sitemaps for {base_domain}...")

        all_urls = []
        for sitemap_url in sitemap_urls:
            try:
                urls = self._parse_sitemap(sitemap_url, depth=1)
                all_urls.extend(urls)
            except Exception as e:
                print(f"Failed to parse sitemap {sitemap_url}: {e}")

        return all_urls

    async def _discover_sitemaps_stealth(self, base_url):
        """Fetch all sitemaps through a single persistent CamoFox browser."""
        renderer = self.stealth_fetcher  # This is the CamoFoxRenderer instance
        await renderer.start()
        try:
            # Replace stealth_fetcher with an async-aware fetcher for this session
            original_fetch = self._fetch
            async def async_fetch(url):
                try:
                    content, status = await renderer.render_page(url, wait_time=1, timeout=15)
                    if status == 200 and content:
                        return status, content.encode('utf-8') if isinstance(content, str) else content
                except Exception as e:
                    print(f"Stealth fetch failed for {url}: {e}")
                # Fallback to regular session
                response = self.session.get(url, timeout=self.timeout)
                return response.status_code, response.content

            # Run the sync discovery logic but with async fetches
            parsed_base = urlparse(base_url)
            base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

            sitemap_urls = [
                f"{base_domain}/sitemap.xml",
                f"{base_domain}/sitemap_index.xml",
                f"{base_domain}/sitemaps.xml",
                f"{base_domain}/sitemap/sitemap.xml"
            ]

            # Fetch robots.txt for sitemaps
            try:
                robots_url = f"{base_domain}/robots.txt"
                status, content = await async_fetch(robots_url)
                if status == 200:
                    text = content.decode('utf-8', errors='ignore')
                    for line in text.split('\n'):
                        line = line.strip()
                        if line.lower().startswith('sitemap:'):
                            sitemap_urls.append(line.split(':', 1)[1].strip())
            except Exception as e:
                print(f"Could not fetch robots.txt: {e}")

            print(f"Discovering sitemaps for {base_domain}...")

            all_urls = []
            await self._parse_sitemap_async(sitemap_urls, all_urls, async_fetch)
            return all_urls
        finally:
            await renderer.stop()

    async def _parse_sitemap_async(self, sitemap_urls, all_urls, fetch_fn, depth=1, max_depth=10):
        """Recursively parse sitemaps using async fetch."""
        if depth > max_depth:
            return
        for sitemap_url in sitemap_urls:
            try:
                print(f"Parsing sitemap: {sitemap_url}")
                status, content = await fetch_fn(sitemap_url)
                if status != 200:
                    print(f"Sitemap blocked: {sitemap_url} returned {status}")
                    continue

                if sitemap_url.endswith('.gz'):
                    try:
                        content = gzip.decompress(content)
                    except:
                        pass

                try:
                    root = ET.fromstring(content)
                except ET.ParseError as e:
                    print(f"XML parse error for {sitemap_url}: {e}")
                    continue

                for elem in root.iter():
                    if '}' in elem.tag:
                        elem.tag = elem.tag.split('}')[1]

                # Nested sitemap index
                sitemaps = root.findall('.//sitemap')
                if sitemaps:
                    nested = [s.find('loc').text.strip() for s in sitemaps if s.find('loc') is not None and s.find('loc').text]
                    print(f"Found sitemap index with {len(nested)} nested sitemaps")
                    await self._parse_sitemap_async(nested, all_urls, fetch_fn, depth + 1, max_depth)

                # URLs
                urls = root.findall('.//url')
                if urls:
                    found = [u.find('loc').text.strip() for u in urls if u.find('loc') is not None and u.find('loc').text]
                    print(f"Found {len(found)} URLs in sitemap")
                    all_urls.extend(found)

            except Exception as e:
                print(f"Error parsing sitemap {sitemap_url}: {e}")

    def _fetch(self, url):
        """Fetch URL content. Uses stealth browser if available, otherwise HTTP session."""
        if self.stealth_fetcher:
            try:
                content, status = self.stealth_fetcher(url)
                if status == 200 and content:
                    return status, content.encode('utf-8') if isinstance(content, str) else content
            except Exception as e:
                print(f"Stealth fetch failed for {url}: {e}")
        # Fallback to regular session
        response = self.session.get(url, timeout=self.timeout)
        return response.status_code, response.content

    def _get_sitemaps_from_robots(self, base_domain):
        """Extract sitemap URLs from robots.txt"""
        sitemaps = []
        try:
            robots_url = f"{base_domain}/robots.txt"
            status, content = self._fetch(robots_url)

            if status == 200:
                text = content.decode('utf-8', errors='ignore')
                for line in text.split('\n'):
                    line = line.strip()
                    if line.lower().startswith('sitemap:'):
                        sitemap_url = line.split(':', 1)[1].strip()
                        sitemaps.append(sitemap_url)

        except Exception as e:
            print(f"Could not fetch robots.txt: {e}")

        return sitemaps

    def _parse_sitemap(self, sitemap_url, depth=1, max_depth=10):
        """
        Parse a sitemap.xml file and extract URLs

        Returns:
            list: List of URLs found in the sitemap
        """
        if depth > max_depth:
            return []

        try:
            print(f"Parsing sitemap: {sitemap_url}")
            status, content = self._fetch(sitemap_url)

            if status != 200:
                print(f"Sitemap blocked: {sitemap_url} returned {status}")
                return []

            # Handle compressed sitemaps
            if sitemap_url.endswith('.gz'):
                try:
                    content = gzip.decompress(content)
                except:
                    pass

            # Parse XML
            try:
                root = ET.fromstring(content)
            except ET.ParseError as e:
                print(f"XML parse error for {sitemap_url}: {e}")
                return []

            # Remove namespace prefixes for easier parsing
            for elem in root.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}')[1]

            all_urls = []

            # Check if this is a sitemap index (contains other sitemaps)
            sitemaps = root.findall('.//sitemap')
            if sitemaps:
                print(f"Found sitemap index with {len(sitemaps)} nested sitemaps")
                for sitemap in sitemaps:
                    loc_elem = sitemap.find('loc')
                    if loc_elem is not None and loc_elem.text:
                        nested_url = loc_elem.text.strip()
                        nested_urls = self._parse_sitemap(nested_url, depth + 1, max_depth)
                        all_urls.extend(nested_urls)

            # Extract URLs from sitemap
            urls = root.findall('.//url')
            if urls:
                print(f"Found {len(urls)} URLs in sitemap")
                for url_elem in urls:
                    loc_elem = url_elem.find('loc')
                    if loc_elem is not None and loc_elem.text:
                        url = loc_elem.text.strip()
                        all_urls.append(url)

            return all_urls

        except Exception as e:
            print(f"Error parsing sitemap {sitemap_url}: {e}")
            return []
