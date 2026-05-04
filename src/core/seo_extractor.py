"""SEO data extraction from HTML content"""
import re
import json
import copy
import requests
import trafilatura
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Comment


class SEOExtractor:
    """Extracts SEO-related data from HTML content"""

    @staticmethod
    def extract_basic_seo_data(soup, result):
        """Extract basic SEO data (title, headings, meta description, etc.)"""
        # Extract title
        title_tag = soup.find('title')
        result['title'] = title_tag.get_text().strip() if title_tag else ''

        # Extract meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        result['meta_description'] = meta_desc.get('content', '').strip() if meta_desc else ''

        # Extract headings
        h1_tag = soup.find('h1')
        result['h1'] = h1_tag.get_text().strip() if h1_tag else ''

        h2_tags = soup.find_all('h2')
        result['h2'] = [h2.get_text().strip() for h2 in h2_tags[:10]]

        h3_tags = soup.find_all('h3')
        result['h3'] = [h3.get_text().strip() for h3 in h3_tags[:10]]

        # Count words
        text_content = soup.get_text()
        words = re.findall(r'\b\w+\b', text_content)
        result['word_count'] = len(words)

        # Extract language
        html_tag = soup.find('html')
        result['lang'] = html_tag.get('lang', '') if html_tag else ''

        # Extract charset
        charset_meta = soup.find('meta', attrs={'charset': True})
        if charset_meta:
            result['charset'] = charset_meta.get('charset', '')
        else:
            content_type_meta = soup.find('meta', attrs={'http-equiv': 'Content-Type'})
            if content_type_meta:
                content = content_type_meta.get('content', '')
                charset_match = re.search(r'charset=([^;]+)', content)
                result['charset'] = charset_match.group(1) if charset_match else ''

    # Tags and class/id patterns to strip before content extraction
    _BOILERPLATE_TAGS = ['nav', 'header', 'footer', 'aside', 'noscript', 'svg',
                         'form', 'iframe', 'dialog']
    _BOILERPLATE_PATTERNS = re.compile(
        r'(nav|header|footer|sidebar|menu|cookie|banner|popup|modal|breadcrumb|'
        r'social|share|widget|advertisement|ad-|ads-|advert|newsletter|signup|'
        r'subscribe|related-posts|comment|'
        r'consent|gdpr|onetrust|cc-banner|cc-window|privacy-notice|'
        r'CookieConsent|cookie-notice|cookie-law|cookie-bar|cookie-alert)',
        re.IGNORECASE
    )

    @staticmethod
    def _clean_soup(html_content):
        """Shared boilerplate removal. Returns cleaned BeautifulSoup object."""
        if not html_content:
            return None

        try:
            clean_soup = BeautifulSoup(html_content, 'html.parser')

            # Remove script/style
            for tag in clean_soup.find_all(['script', 'style']):
                tag.decompose()

            # Remove HTML comments
            for comment in clean_soup.find_all(string=lambda t: isinstance(t, Comment)):
                comment.extract()

            # Remove semantic boilerplate tags
            for tag_name in SEOExtractor._BOILERPLATE_TAGS:
                for tag in clean_soup.find_all(tag_name):
                    tag.decompose()

            # Remove elements with boilerplate class/id names
            for tag in clean_soup.find_all(True):
                classes = ' '.join(tag.get('class', []))
                tag_id = tag.get('id', '')
                if SEOExtractor._BOILERPLATE_PATTERNS.search(classes) or \
                   SEOExtractor._BOILERPLATE_PATTERNS.search(tag_id):
                    tag.decompose()

            return clean_soup
        except Exception:
            return BeautifulSoup(html_content, 'html.parser')

    @staticmethod
    def extract_body_text(html_content, result):
        """Extract clean main body text using pre-clean + trafilatura."""
        import unicodedata

        if not html_content:
            result['body_text'] = ''
            return

        clean_soup = SEOExtractor._clean_soup(html_content)
        if not clean_soup:
            result['body_text'] = ''
            return

        try:
            cleaned_html = str(clean_soup)
            body = trafilatura.extract(
                cleaned_html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_precision=True,
            )
            text = (body or '').strip()
            text = unicodedata.normalize('NFKC', text)
            text = re.sub(r'\n{3,}', '\n\n', text)
            result['body_text'] = text
        except Exception:
            result['body_text'] = ''

    @staticmethod
    def extract_sections(html_content, title='', clean_text=''):
        """Split page content into heading-delimited sections for embedding.

        Uses trafilatura's clean output (clean_text) as the source, split by
        heading text markers found in the DOM. This ensures sections have the
        same quality as full_text — no boilerplate, no nav/footer/scripts.

        If clean_text is not provided, falls back to trafilatura extraction.

        Returns list of {heading, heading_level, text, word_count, position}.
        Sections < 30 words are merged into the previous section.
        Pages with no headings return a single section with the full text.
        """
        import unicodedata

        clean_soup = SEOExtractor._clean_soup(html_content)
        if not clean_soup:
            return []

        # Get clean text from trafilatura if not provided
        if not clean_text:
            try:
                cleaned_html = str(clean_soup)
                clean_text = trafilatura.extract(
                    cleaned_html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                    favor_precision=True,
                ) or ''
                clean_text = unicodedata.normalize('NFKC', clean_text).strip()
            except Exception:
                clean_text = ''

        if not clean_text:
            return []

        # Find all H2 and H3 heading texts from DOM (in order)
        headings_info = []
        for h in clean_soup.find_all(['h2', 'h3']):
            h_text = h.get_text(strip=True)
            if h_text:
                headings_info.append({
                    'heading': h_text,
                    'heading_level': int(h.name[1]),
                })

        if not headings_info:
            # No headings — single section with all clean text
            words = re.findall(r'\b\w+\b', clean_text)
            return [{
                'heading': title or 'Introduction',
                'heading_level': 1,
                'text': clean_text,
                'word_count': len(words),
                'position': 0,
            }]

        # Split clean_text by heading markers
        # Strategy: find each heading text in the clean text and split there
        sections = []
        remaining_text = clean_text

        # Content before first heading = intro
        first_heading = headings_info[0]['heading']
        first_pos = remaining_text.find(first_heading)

        if first_pos > 0:
            intro_text = remaining_text[:first_pos].strip()
            intro_text = re.sub(r'\n{3,}', '\n\n', intro_text).strip()
            if intro_text:
                sections.append({
                    'heading': title or 'Introduction',
                    'heading_level': 1,
                    'text': intro_text,
                    'word_count': len(re.findall(r'\b\w+\b', intro_text)),
                })

        # Split by each heading
        for i, h_info in enumerate(headings_info):
            h_text = h_info['heading']
            h_level = h_info['heading_level']

            # Find this heading in the remaining text
            h_pos = remaining_text.find(h_text)
            if h_pos == -1:
                continue

            # Find the next heading to determine the end boundary
            content_start = h_pos + len(h_text)
            content_end = len(remaining_text)

            for next_h in headings_info[i + 1:]:
                next_pos = remaining_text.find(next_h['heading'], content_start)
                if next_pos != -1:
                    content_end = next_pos
                    break

            section_text = remaining_text[content_start:content_end].strip()
            section_text = re.sub(r'\n{3,}', '\n\n', section_text).strip()

            sections.append({
                'heading': h_text,
                'heading_level': h_level,
                'text': section_text,
                'word_count': len(re.findall(r'\b\w+\b', section_text)),
            })

        # Merge small sections (< 30 words) into previous
        merged = []
        for section in sections:
            if merged and section['word_count'] < 30:
                merged[-1]['text'] += '\n\n' + section['text']
                merged[-1]['word_count'] += section['word_count']
            else:
                merged.append(section)

        # Assign positions
        for i, section in enumerate(merged):
            section['position'] = i

        return merged

    @staticmethod
    def _table_to_text(table_element):
        """Convert a table to readable row-by-row text."""
        rows = []
        for tr in table_element.find_all('tr'):
            cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
            if any(cells):
                rows.append(' | '.join(cells))
        return '\n'.join(rows)

    @staticmethod
    def extract_meta_tags(soup, result):
        """Extract all meta tags"""
        meta_tags = soup.find_all('meta')

        for meta in meta_tags:
            name = meta.get('name', '').lower()
            content = meta.get('content', '')

            if name:
                result['meta_tags'][name] = content

                # Extract specific important meta tags
                if name == 'viewport':
                    result['viewport'] = content
                elif name == 'robots':
                    result['robots'] = content
                elif name == 'author':
                    result['author'] = content
                elif name == 'keywords':
                    result['keywords'] = content
                elif name == 'generator':
                    result['generator'] = content
                elif name == 'theme-color':
                    result['theme_color'] = content

        # Extract canonical URL
        canonical = soup.find('link', attrs={'rel': 'canonical'})
        result['canonical_url'] = canonical.get('href', '') if canonical else ''

    @staticmethod
    def extract_opengraph_tags(soup, result):
        """Extract OpenGraph meta tags"""
        og_metas = soup.find_all('meta', attrs={'property': re.compile(r'^og:')})

        for meta in og_metas:
            property_name = meta.get('property', '')
            content = meta.get('content', '')
            if property_name:
                key = property_name.replace('og:', '')
                result['og_tags'][key] = content

    @staticmethod
    def extract_twitter_tags(soup, result):
        """Extract Twitter Card meta tags"""
        twitter_metas = soup.find_all('meta', attrs={'name': re.compile(r'^twitter:')})

        for meta in twitter_metas:
            name = meta.get('name', '')
            content = meta.get('content', '')
            if name:
                key = name.replace('twitter:', '')
                result['twitter_tags'][key] = content

    @staticmethod
    def extract_json_ld(soup, result):
        """Extract JSON-LD structured data"""
        json_ld_scripts = soup.find_all('script', attrs={'type': 'application/ld+json'})

        for script in json_ld_scripts:
            try:
                json_data = json.loads(script.string)
                result['json_ld'].append(json_data)
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue

    @staticmethod
    def extract_analytics_tracking(soup, html_content, result):
        """Detect analytics and tracking scripts"""
        # Google Analytics patterns
        ga_patterns = [
            r'gtag\(',
            r'ga\(',
            r'GoogleAnalyticsObject',
            r'google-analytics\.com',
            r'googletagmanager\.com'
        ]

        # GA4 ID pattern
        ga4_match = re.search(r'G-[A-Z0-9]{10}', html_content)
        if ga4_match:
            result['analytics']['ga4_id'] = ga4_match.group()
            result['analytics']['gtag'] = True

        # GTM ID pattern
        gtm_match = re.search(r'GTM-[A-Z0-9]+', html_content)
        if gtm_match:
            result['analytics']['gtm_id'] = gtm_match.group()

        # Check for various analytics
        for pattern in ga_patterns:
            if re.search(pattern, html_content, re.IGNORECASE):
                result['analytics']['google_analytics'] = True
                break

        # Facebook Pixel
        if re.search(r'fbq\(|facebook\.com/tr', html_content, re.IGNORECASE):
            result['analytics']['facebook_pixel'] = True

        # Hotjar
        if re.search(r'hotjar\.com|hj\(', html_content, re.IGNORECASE):
            result['analytics']['hotjar'] = True

        # Mixpanel
        if re.search(r'mixpanel\.com|mixpanel\.track', html_content, re.IGNORECASE):
            result['analytics']['mixpanel'] = True

    @staticmethod
    def extract_images(soup, base_url, result, http_session=None):
        """Extract image information with optional HEAD requests for file metadata"""
        images = soup.find_all('img')
        consecutive_timeouts = 0

        for img in images[:20]:  # Limit to first 20 images
            src = img.get('src', '')
            alt = img.get('alt', '')

            if src:
                # Skip non-HTTP sources (data: URIs, blob: URIs)
                if src.startswith(('data:', 'blob:')):
                    result['images'].append({
                        'src': src[:120],  # Truncate long data URIs
                        'alt': alt,
                        'width': img.get('width', ''),
                        'height': img.get('height', ''),
                        'file_size': len(src) if src.startswith('data:') else 0,
                        'content_type': 'inline/base64' if src.startswith('data:') else 'blob',
                        'loading': img.get('loading', ''),
                    })
                    continue

                # Convert relative URLs to absolute
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    parsed_base = urlparse(base_url)
                    src = f"{parsed_base.scheme}://{parsed_base.netloc}{src}"
                elif not src.startswith(('http://', 'https://')):
                    src = urljoin(base_url, src)

                img_data = {
                    'src': src,
                    'alt': alt,
                    'width': img.get('width', ''),
                    'height': img.get('height', ''),
                    'file_size': 0,
                    'content_type': '',
                    'loading': img.get('loading', ''),
                }

                # HEAD request for file size and content type
                # Skip if 3+ consecutive timeouts on this page (CDN likely blocking)
                if http_session and src.startswith(('http://', 'https://')) and consecutive_timeouts < 3:
                    try:
                        head = http_session.head(src, timeout=5, allow_redirects=True)

                        # Fall back to GET with Range header if HEAD is rejected
                        if head.status_code in (403, 405):
                            head = http_session.get(
                                src, timeout=5, allow_redirects=True,
                                headers={'Range': 'bytes=0-0'},
                                stream=True,
                            )
                            head.close()

                        if head.status_code < 400:
                            img_data['file_size'] = int(head.headers.get('Content-Length', 0))
                            img_data['content_type'] = head.headers.get('Content-Type', '')
                            consecutive_timeouts = 0
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                        consecutive_timeouts += 1
                    except Exception:
                        pass  # Other errors - keep image entry with zeros

                result['images'].append(img_data)

    @staticmethod
    def extract_link_counts(soup, result, base_domain):
        """Count internal vs external links"""
        links = soup.find_all('a', href=True)

        for link in links:
            href = link.get('href', '')
            if href and not href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
                absolute_url = urljoin(result['url'], href)
                parsed_url = urlparse(absolute_url)

                # Handle www vs non-www domains
                url_domain_clean = parsed_url.netloc.replace('www.', '', 1)
                base_domain_clean = base_domain.replace('www.', '', 1)

                if url_domain_clean == base_domain_clean:
                    result['internal_links'] += 1
                else:
                    result['external_links'] += 1

    @staticmethod
    def extract_hreflang(soup, result):
        """Extract hreflang links"""
        hreflang_links = soup.find_all('link', attrs={'rel': 'alternate', 'hreflang': True})

        for link in hreflang_links:
            hreflang = link.get('hreflang', '')
            href = link.get('href', '')
            if hreflang and href:
                result['hreflang'].append({
                    'lang': hreflang,
                    'url': href
                })

    @staticmethod
    def extract_schema_org(soup, result):
        """Extract Schema.org microdata"""
        schema_items = soup.find_all(attrs={'itemtype': True})

        for item in schema_items:
            itemtype = item.get('itemtype', '')
            if itemtype:
                result['schema_org'].append({
                    'type': itemtype,
                    'properties': SEOExtractor._extract_microdata_properties(item)
                })

    @staticmethod
    def _extract_microdata_properties(element):
        """Extract microdata properties from an element"""
        properties = {}

        # Find all elements with itemprop
        prop_elements = element.find_all(attrs={'itemprop': True})

        for prop_elem in prop_elements:
            prop_name = prop_elem.get('itemprop', '')

            # Get content based on element type
            if prop_elem.name in ['meta']:
                content = prop_elem.get('content', '')
            elif prop_elem.name in ['img']:
                content = prop_elem.get('src', '')
            elif prop_elem.name in ['a']:
                content = prop_elem.get('href', '')
            else:
                content = prop_elem.get_text().strip()

            if prop_name and content:
                properties[prop_name] = content

        return properties

    @staticmethod
    def create_empty_result(url, depth, status_code=0, error=None):
        """Create an empty result structure"""
        return {
            'url': url,
            'status_code': status_code,
            'content_type': '',
            'size': 0,
            'is_internal': False,
            'depth': depth,
            'title': '',
            'meta_description': '',
            'h1': '',
            'h2': [],
            'h3': [],
            'word_count': 0,
            'meta_tags': {},
            'og_tags': {},
            'twitter_tags': {},
            'canonical_url': '',
            'lang': '',
            'charset': '',
            'viewport': '',
            'robots': '',
            'author': '',
            'keywords': '',
            'generator': '',
            'theme_color': '',
            'json_ld': [],
            'analytics': {
                'google_analytics': False,
                'gtag': False,
                'ga4_id': '',
                'gtm_id': '',
                'facebook_pixel': False,
                'hotjar': False,
                'mixpanel': False
            },
            'images': [],
            'external_links': 0,
            'internal_links': 0,
            'response_time': 0,
            'redirects': [],
            'hreflang': [],
            'schema_org': [],
            'linked_from': [],
            'body_text': '',
            'error': error
        }
