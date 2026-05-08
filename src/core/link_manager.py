"""Link management and extraction"""
import threading
from urllib.parse import urljoin, urlparse
from collections import deque

SOCIAL_DOMAINS = frozenset([
    'facebook.com', 'twitter.com', 'x.com', 'linkedin.com', 'instagram.com',
    'youtube.com', 'tiktok.com', 'pinterest.com', 'reddit.com', 'github.com',
    'threads.net', 'mastodon.social', 'bsky.app',
])

# Two-level taxonomy: placement_detail (17 types) → placement (4 broad categories)
# The broad category preserves backward compat with internal-linking parser:
#   body_links = [lk for lk in links if lk.get("placement") == "body"]
PLACEMENT_TO_CATEGORY = {
    'body': 'body',        'button': 'body',       'cta': 'body',
    'image': 'body',       'icon': 'body',         'card': 'body',
    'banner': 'body',
    'navigation': 'nav',   'menu-dropdown': 'nav', 'breadcrumb': 'nav',
    'sidebar': 'nav',      'pagination': 'nav',    'language-switcher': 'nav',
    'footer': 'footer',    'social': 'footer',
    'logo': 'header',      'skip-link': 'header',
}

# Body sub-type refinement based on parent HTML tag
PARENT_TAG_TO_BODY_DETAIL = {
    'p': 'body_paragraph',
    'li': 'body_list',
    'td': 'body_table',
    'th': 'body_table',
    'blockquote': 'body_blockquote',
    'figcaption': 'body_caption',
    'h1': 'body_heading',
    'h2': 'body_heading',
    'h3': 'body_heading',
    'h4': 'body_heading',
    'h5': 'body_heading',
    'h6': 'body_heading',
}


class LinkManager:
    """Manages link discovery, tracking, and extraction"""

    def __init__(self, base_domain):
        self.base_domain = base_domain
        self.visited_urls = set()
        self.discovered_urls = deque()
        self.all_discovered_urls = set()
        self.all_links = []
        self.links_set = set()
        self.source_pages = {}  # Maps target_url -> list of source_urls

        self.urls_lock = threading.Lock()
        self.links_lock = threading.Lock()

    def extract_links(self, soup, current_url, depth, should_crawl_callback):
        """Extract links from HTML and add to discovery queue"""
        links = soup.find_all('a', href=True)

        for link in links:
            href = link['href'].strip()
            if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                continue

            # Convert relative URLs to absolute
            absolute_url = urljoin(current_url, href)

            # Clean URL (remove fragment)
            parsed = urlparse(absolute_url)
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean_url += f"?{parsed.query}"

            # Thread-safe checking and adding
            with self.urls_lock:
                # Track source page for this URL
                if clean_url not in self.source_pages:
                    self.source_pages[clean_url] = []
                if current_url not in self.source_pages[clean_url]:
                    self.source_pages[clean_url].append(current_url)

                if (clean_url not in self.visited_urls and
                    clean_url not in self.all_discovered_urls and
                    clean_url != current_url):

                    # Check if this URL should be crawled
                    if should_crawl_callback(clean_url):
                        self.all_discovered_urls.add(clean_url)
                        self.discovered_urls.append((clean_url, depth))

    def collect_all_links(self, soup, source_url, crawl_results):
        """Collect all links for the Links tab display"""
        links = soup.find_all('a', href=True)

        for link in links:
            href = link['href'].strip()
            if not href or href.startswith('#'):
                continue

            # Get anchor text
            anchor_text = link.get_text().strip()[:100]

            # Handle special link types
            if href.startswith('mailto:') or href.startswith('tel:'):
                continue

            # Convert relative URLs to absolute
            try:
                absolute_url = urljoin(source_url, href)
                parsed_target = urlparse(absolute_url)

                # Clean URL (remove fragment)
                clean_url = f"{parsed_target.scheme}://{parsed_target.netloc}{parsed_target.path}"
                if parsed_target.query:
                    clean_url += f"?{parsed_target.query}"

                # Determine if link is internal or external
                target_domain_clean = parsed_target.netloc.replace('www.', '', 1)
                base_domain_clean = self.base_domain.replace('www.', '', 1)
                is_internal = target_domain_clean == base_domain_clean

                # Find the status of the target URL if we've crawled it
                target_status = None
                for result in crawl_results:
                    if result['url'] == clean_url:
                        target_status = result['status_code']
                        break

                # Determine placement (two-level taxonomy)
                placement, placement_detail = self.classify_link_placement(link)

                link_data = {
                    'source_url': source_url,
                    'target_url': clean_url,
                    'anchor_text': anchor_text or '(no text)',
                    'is_internal': is_internal,
                    'target_domain': parsed_target.netloc,
                    'target_status': target_status,
                    'placement': placement,
                    'placement_detail': placement_detail,
                }

                # Track source page for this URL (for "Linked From" feature)
                with self.urls_lock:
                    if clean_url not in self.source_pages:
                        self.source_pages[clean_url] = []
                    if source_url not in self.source_pages[clean_url]:
                        self.source_pages[clean_url].append(source_url)

                # Thread-safe adding to links collection with duplicate checking
                with self.links_lock:
                    link_key = f"{link_data['source_url']}|{link_data['target_url']}"

                    if link_key not in self.links_set:
                        self.links_set.add(link_key)
                        self.all_links.append(link_data)

            except Exception:
                continue

    def _detect_link_placement(self, link_element):
        """Classify where on the page a link appears.

        Priority-ordered: most-specific first, first match wins.
        Returns one of 17 placement types (default: 'body').
        """
        # --- Element-level checks (inspect the <a> itself) ---

        # 1. Skip link
        anchor_text = link_element.get_text(strip=True).lower()
        if anchor_text in ('skip to content', 'skip to main content', 'skip to main', 'skip navigation'):
            return 'skip-link'
        link_classes = link_element.get('class', [])
        link_classes_set = {c.lower() for c in link_classes} if link_classes else set()
        if link_classes_set & {'sr-only', 'visually-hidden', 'skip-link', 'screen-reader-text'}:
            if 'skip' in anchor_text or 'main' in anchor_text or 'content' in anchor_text:
                return 'skip-link'

        # 6. Social — check href domain before walking ancestors
        href = link_element.get('href', '')
        for domain in SOCIAL_DOMAINS:
            if domain in href:
                return 'social'

        # 7. Icon — <a> with only icon children, no visible text
        children = [c for c in link_element.children if getattr(c, 'name', None) or (isinstance(c, str) and c.strip())]
        visible_text = link_element.get_text(strip=True)
        if children and not visible_text:
            child_tags = {getattr(c, 'name', None) for c in children}
            child_tags.discard(None)
            if child_tags <= {'i', 'svg', 'span'}:
                for child in children:
                    child_cls = ' '.join(child.get('class', [])).lower() if getattr(child, 'get', None) else ''
                    if any(p in child_cls for p in ('fa-', 'icon', 'material', 'glyphicon')):
                        return 'icon'
                    if getattr(child, 'get', None) and child.get('aria-hidden') == 'true':
                        return 'icon'

        # 8. Image — <a> wrapping only img/picture/svg/figure
        #    Defer to ancestor walk (might be logo)
        is_image_only = False
        if children:
            child_tags = {getattr(c, 'name', None) for c in children}
            child_tags.discard(None)
            text_children = [c for c in children if not getattr(c, 'name', None) and isinstance(c, str) and c.strip()]
            if child_tags <= {'img', 'picture', 'svg', 'figure'} and not text_children:
                is_image_only = True

        # --- Ancestor chain walk (collect context in one pass) ---
        ancestor_context = self._collect_ancestor_context(link_element)

        # 2. Logo — link inside header with logo class (image-only OR text logos)
        logo_classes = {'site-logo', 'custom-logo-link', 'navbar-brand', 'brand', 'logo'}
        if ancestor_context['in_header'] and (ancestor_context['has_logo_class'] or link_classes_set & logo_classes):
            return 'logo'

        # 8b. Now safe to return image (not a logo)
        if is_image_only:
            return 'image'

        # 3. Language switcher
        if ancestor_context['has_lang_switcher_class'] or link_element.get('hreflang'):
            return 'language-switcher'

        # 4. Breadcrumb
        if ancestor_context['has_breadcrumb']:
            return 'breadcrumb'

        # 5. Pagination
        rel = link_element.get('rel', [])
        rel_str = ' '.join(rel) if isinstance(rel, list) else (rel or '')
        if ancestor_context['has_pagination'] or 'prev' in rel_str or 'next' in rel_str:
            return 'pagination'

        # 9. CTA — btn/button class inside hero/banner, or cta class on ancestor
        if ancestor_context['has_cta_class']:
            return 'cta'
        if ancestor_context['has_banner'] and any('button' in c or 'btn' in c for c in link_classes_set):
            return 'cta'

        # 10. Button — universal substring match on <a> classes
        if link_element.get('role') == 'button':
            return 'button'
        if any('button' in c or 'btn' in c for c in link_classes_set):
            return 'button'
        if link_element.find('button'):
            return 'button'

        # 11. Footer — checked BEFORE sidebar/nav
        if ancestor_context['in_footer']:
            return 'footer'

        # 12. Sidebar
        if ancestor_context['has_sidebar']:
            return 'sidebar'

        # 13. Banner (not already caught by CTA)
        if ancestor_context['has_banner']:
            return 'banner'

        # 14. Card
        if ancestor_context['has_card']:
            return 'card'

        # 15. Menu dropdown — inside nav/header with dropdown class
        if ancestor_context['in_nav_or_header'] and ancestor_context['has_dropdown']:
            return 'menu-dropdown'

        # 16. Navigation
        if ancestor_context['in_nav_or_header']:
            return 'navigation'

        # 17. Default
        return 'body'

    def classify_link_placement(self, link_element):
        """Return (placement, placement_detail) two-level taxonomy.

        placement: one of 4 broad categories (body, nav, footer, header)
        placement_detail: one of 17 specific types (body_paragraph, nav_breadcrumb, etc.)

        For body links, placement_detail is further refined by parent HTML tag.
        """
        detail_type = self._detect_link_placement(link_element)
        category = PLACEMENT_TO_CATEGORY.get(detail_type, 'body')

        # Refine body links by parent tag for SEO sub-typing
        if category == 'body' and detail_type == 'body':
            parent = link_element.parent
            while parent and parent.name not in PARENT_TAG_TO_BODY_DETAIL and parent.name not in ('div', 'body', None):
                parent = parent.parent
            if parent and parent.name in PARENT_TAG_TO_BODY_DETAIL:
                detail_type = PARENT_TAG_TO_BODY_DETAIL[parent.name]
            else:
                detail_type = 'body_paragraph'  # default body sub-type
        elif category == 'body' and detail_type == 'image':
            detail_type = 'body_image'
        elif category == 'body' and detail_type in ('button', 'cta'):
            detail_type = 'body_cta'
        elif category == 'body' and detail_type == 'icon':
            detail_type = 'body_cta'
        elif category == 'body' and detail_type in ('card', 'banner'):
            # Refine card/banner links by parent tag (e.g., h3 link in a card = body_heading)
            parent = link_element.parent
            while parent and parent.name not in PARENT_TAG_TO_BODY_DETAIL and parent.name not in ('div', 'body', None):
                parent = parent.parent
            if parent and parent.name in PARENT_TAG_TO_BODY_DETAIL:
                detail_type = PARENT_TAG_TO_BODY_DETAIL[parent.name]
            else:
                detail_type = 'body_paragraph'
        elif category == 'nav' and detail_type == 'breadcrumb':
            detail_type = 'nav_breadcrumb'
        elif category == 'nav' and detail_type == 'sidebar':
            detail_type = 'nav_sidebar'
        elif category == 'nav' and detail_type in ('menu-dropdown', 'language-switcher', 'pagination'):
            detail_type = 'nav_secondary'
        elif category == 'nav' and detail_type == 'navigation':
            detail_type = 'nav_main'
        elif category == 'footer':
            detail_type = 'footer_link'
        elif category == 'header' and detail_type == 'logo':
            detail_type = 'header_logo'
        elif category == 'header':
            detail_type = 'header_cta'

        return category, detail_type

    @staticmethod
    def _collect_ancestor_context(link_element):
        """Walk ancestor chain once, collecting all context flags."""
        ctx = {
            'in_header': False,
            'in_footer': False,
            'in_nav_or_header': False,
            'has_logo_class': False,
            'has_lang_switcher_class': False,
            'has_breadcrumb': False,
            'has_pagination': False,
            'has_cta_class': False,
            'has_banner': False,
            'has_sidebar': False,
            'has_card': False,
            'has_dropdown': False,
        }

        current = link_element.parent
        while current and current.name:
            tag = current.name
            # Stop at <body>/<html> — their classes are page-level, not structural
            if tag in ('body', 'html'):
                break
            classes = current.get('class', [])
            cls_set = {c.lower() for c in classes} if classes else set()
            el_id = (current.get('id') or '').lower()

            # Semantic tags + ARIA landmark roles
            role = current.get('role', '')
            if tag == 'nav' or role == 'navigation':
                ctx['in_nav_or_header'] = True
            if tag == 'header' or role == 'banner':
                ctx['in_header'] = True
                ctx['in_nav_or_header'] = True
            if tag == 'footer' or role == 'contentinfo':
                ctx['in_footer'] = True
            if tag == 'aside' or role == 'complementary':
                ctx['has_sidebar'] = True

            # Class token checks — pattern-based for broad CMS coverage
            for cls in cls_set:
                if (cls.startswith('nav') or cls.endswith('-nav')
                        or cls.startswith('menu') or cls.endswith('-menu')
                        or cls in ('site-header',)):
                    ctx['in_nav_or_header'] = True
                if 'foot' in cls and 'note' not in cls:
                    ctx['in_footer'] = True
                if cls in ('site-logo', 'custom-logo', 'custom-logo-link',
                           'site-branding', 'navbar-brand',
                           'wp-block-site-logo', 'gh-head-logo',
                           'header__heading-logo'):
                    ctx['has_logo_class'] = True
                # Substring 'logo' only on CLOSE ancestors (parent/grandparent)
                if 'logo' in cls and current == link_element.parent:
                    ctx['has_logo_class'] = True
                if cls in ('language-switcher', 'lang-switcher', 'language-selector',
                           'wpml-ls', 'polylang-switcher', 'lang-toggle'):
                    ctx['has_lang_switcher_class'] = True
                if 'breadcrumb' in cls:
                    ctx['has_breadcrumb'] = True
                if cls in ('pagination', 'pager', 'page-numbers'):
                    ctx['has_pagination'] = True
                if 'cta' in cls or cls == 'call-to-action':
                    ctx['has_cta_class'] = True
                if cls in ('hero', 'banner', 'jumbotron', 'promo', 'masthead',
                           'announcement-bar', 'announcement', 'promo-bar',
                           'hero-section', 'hero-banner'):
                    ctx['has_banner'] = True
                if 'sidebar' in cls or 'widget' in cls or cls in ('complementary', 'aside'):
                    ctx['has_sidebar'] = True
                if cls in ('card', 'tile',
                           'wp-block-post',
                           'elementor-widget-posts', 'elementor-widget-portfolio',
                           'sqs-block', 'summary-item', 'blog-item',
                           'product-card', 'product-card-wrapper', 'product',
                           'product-item', 'product-item-info',
                           'product-miniature',
                           'woocommerce-loop-product__link',
                           'card-wrapper', 'article-card',
                           'post-card', 'kg-card',
                           'w-dyn-item',
                           'cmp-teaser',
                           'related-posts', 'related-articles',
                           'recommended', 'you-may-also-like'):
                    ctx['has_card'] = True
                if cls in ('dropdown', 'dropdown-menu', 'submenu', 'sub-menu',
                           'mega-menu', 'dropdown-content', 'w-dropdown'):
                    ctx['has_dropdown'] = True

            # ID-based checks
            if el_id:
                if 'foot' in el_id and 'note' not in el_id:
                    ctx['in_footer'] = True
                if el_id.startswith('nav') or el_id.endswith('-nav') or el_id in ('menu', 'site-header'):
                    ctx['in_nav_or_header'] = True
                if 'sidebar' in el_id or 'widget' in el_id:
                    ctx['has_sidebar'] = True

            # Schema.org breadcrumb
            if current.get('itemtype') and 'BreadcrumbList' in (current.get('itemtype') or ''):
                ctx['has_breadcrumb'] = True
            # Aria label breadcrumb
            if (current.get('aria-label') or '').lower() == 'breadcrumb':
                ctx['has_breadcrumb'] = True
            # aria-expanded (dropdown indicator, only meaningful inside nav/header)
            if current.get('aria-expanded') is not None:
                ctx['has_dropdown'] = True

            current = current.parent

        return ctx

    @staticmethod
    def _find_parent_heading(element):
        """Find the nearest preceding H2/H3 heading for a link element."""
        current = element
        while current:
            sibling = current.find_previous_sibling(['h2', 'h3'])
            if sibling:
                return sibling.get_text(strip=True), int(sibling.name[1])
            current = current.parent
            if current and current.name in ['h2', 'h3']:
                return current.get_text(strip=True), int(current.name[1])
        return None, None

    @staticmethod
    def _extract_link_context(link_element, max_chars=200):
        """Extract surrounding text context from parent block element."""
        parent = link_element.parent
        while parent and parent.name not in ['p', 'li', 'td', 'blockquote', 'div', 'body', None]:
            parent = parent.parent

        if parent and parent.name in ['p', 'li', 'td', 'blockquote']:
            text = parent.get_text(separator=' ', strip=True)
            if len(text) > max_chars:
                return text[:max_chars].rsplit(' ', 1)[0] + '...'
            return text

        if link_element.parent:
            text = link_element.parent.get_text(separator=' ', strip=True)
            if len(text) > max_chars:
                return text[:max_chars].rsplit(' ', 1)[0] + '...'
            return text

        return ''

    @staticmethod
    def _extract_link_attributes(link_element):
        """Extract all HTML attributes from an <a> tag."""
        attrs = link_element.attrs or {}
        result = {
            'rel': ' '.join(attrs.get('rel', [])) if isinstance(attrs.get('rel'), list) else attrs.get('rel'),
            'target': attrs.get('target'),
            'title': attrs.get('title'),
            'class': ' '.join(attrs.get('class', [])) if isinstance(attrs.get('class'), list) else attrs.get('class'),
            'id': attrs.get('id'),
            'data': {},
        }
        for key, value in attrs.items():
            if key.startswith('data-'):
                result['data'][key[5:]] = value
        return result

    def collect_all_links_enriched(self, soup, source_url, sections, crawl_results):
        """Collect all links with rich context for linkgraph mode.

        Unlike collect_all_links, this does NOT deduplicate on source|target alone.
        Dedup key: source_url|target_url|anchor_text|section_position
        """
        links = soup.find_all('a', href=True)

        heading_positions = {}
        for section in sections:
            heading_positions[section.get('heading', '')] = section.get('position')

        for link in links:
            href = link['href'].strip()
            if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                continue

            try:
                absolute_url = urljoin(source_url, href)
                parsed_target = urlparse(absolute_url)

                clean_url = f"{parsed_target.scheme}://{parsed_target.netloc}{parsed_target.path}"
                if parsed_target.query:
                    clean_url += f"?{parsed_target.query}"

                target_domain_clean = parsed_target.netloc.replace('www.', '', 1)
                base_domain_clean = self.base_domain.replace('www.', '', 1)
                is_internal = target_domain_clean == base_domain_clean

                target_status = None
                for r in crawl_results:
                    if r['url'] == clean_url:
                        target_status = r['status_code']
                        break

                placement, placement_detail = self.classify_link_placement(link)
                anchor_text = link.get_text().strip()[:100] or '(no text)'
                context = self._extract_link_context(link)
                parent_heading, _ = self._find_parent_heading(link)
                section_position = heading_positions.get(parent_heading) if parent_heading else None
                attributes = self._extract_link_attributes(link)

                # Derive flags the internal-linking parser needs
                rel_str = ' '.join(link.get('rel', [])) if isinstance(link.get('rel'), list) else (link.get('rel') or '')
                is_nofollow = 'nofollow' in rel_str or 'sponsored' in rel_str
                is_image_link = placement_detail == 'body_image'
                parent_tag = link.parent.name if link.parent else None

                link_data = {
                    'source_url': source_url,
                    'target_url': clean_url,
                    'anchor_text': anchor_text,
                    'is_internal': is_internal,
                    'target_domain': parsed_target.netloc,
                    'target_status': target_status,
                    'placement': placement,
                    'placement_detail': placement_detail,
                    'context': context,
                    'parent_heading': parent_heading or '',
                    'section_position': section_position,
                    'attributes': attributes,
                    'parent_tag': parent_tag,
                    'is_image_link': is_image_link,
                    'is_nofollow': is_nofollow,
                }

                with self.urls_lock:
                    if clean_url not in self.source_pages:
                        self.source_pages[clean_url] = []
                    if source_url not in self.source_pages[clean_url]:
                        self.source_pages[clean_url].append(source_url)

                with self.links_lock:
                    link_key = f"{source_url}|{clean_url}|{anchor_text}|{section_position}"
                    if link_key not in self.links_set:
                        self.links_set.add(link_key)
                        self.all_links.append(link_data)

            except Exception:
                continue

    def is_internal(self, url):
        """Check if URL is internal to the base domain"""
        parsed_url = urlparse(url)
        url_domain_clean = parsed_url.netloc.replace('www.', '', 1)
        base_domain_clean = self.base_domain.replace('www.', '', 1)
        return url_domain_clean == base_domain_clean

    def add_url(self, url, depth):
        """Add a URL to the discovery queue"""
        with self.urls_lock:
            if url not in self.all_discovered_urls and url not in self.visited_urls:
                self.all_discovered_urls.add(url)
                self.discovered_urls.append((url, depth))

    def mark_visited(self, url):
        """Mark a URL as visited"""
        with self.urls_lock:
            self.visited_urls.add(url)

    def get_next_url(self):
        """Get the next URL to crawl"""
        with self.urls_lock:
            if self.discovered_urls:
                return self.discovered_urls.popleft()
        return None

    def get_stats(self):
        """Get current statistics"""
        with self.urls_lock:
            return {
                'discovered': len(self.all_discovered_urls),
                'visited': len(self.visited_urls),
                'pending': len(self.discovered_urls)
            }

    def update_link_statuses(self, crawl_results):
        """Update target_status for all links based on crawl results"""
        # Build a fast lookup dict
        status_lookup = {result['url']: result['status_code'] for result in crawl_results}

        with self.links_lock:
            for link in self.all_links:
                target_url = link['target_url']
                if target_url in status_lookup:
                    link['target_status'] = status_lookup[target_url]

    def get_source_pages(self, url):
        """Get list of source pages that link to this URL"""
        with self.urls_lock:
            return self.source_pages.get(url, []).copy()

    def reset(self):
        """Reset all state"""
        with self.urls_lock:
            self.visited_urls.clear()
            self.discovered_urls.clear()
            self.all_discovered_urls.clear()
            self.source_pages.clear()

        with self.links_lock:
            self.all_links.clear()
            self.links_set.clear()
