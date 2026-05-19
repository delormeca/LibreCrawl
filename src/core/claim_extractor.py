"""
Claim Extractor — extracts verifiable factual claims from crawled page content.
Follows the embeddings module pattern: post-processing, background thread,
progress callback, OpenAI API.
"""

import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

JUNK_URL_PATTERNS = [
    '/page/', '/tag/', '/category/', '/author/', '/archive/',
    '/wp-admin/', '/wp-login', '/wp-json/', '/wp-content/',
    '/search', '/login', '/register', '/cart/', '/checkout/',
    '/account/', '/admin/', '?s=', '?search=', '/feed/',
    '/print/', '/amp/', '/embed/',
]

EXTRACTION_PROMPT = """Extract all verifiable factual claims from this web page content.

Rules:
- A claim is a specific, verifiable statement (not an opinion or marketing fluff)
- Each claim must be self-contained (understandable without the page)
- Preserve the original language (French, English, or Spanish)
- Include the exact source sentence each claim came from
- Skip vague statements like "best quality" or "we care about you"
- DO extract: prices, policies, hours, credentials, stats, locations, product specs

Return a JSON object with a "claims" key containing the array:
{"claims": [{"claim": "...", "source_text": "..."}]}"""

MIN_WORD_COUNT = 100
MAX_INPUT_TOKENS = 100_000


def filter_eligible_pages(pages):
    """Filter pages eligible for claim extraction.
    Criteria: status_code=200, word_count>=100, body_text not empty,
    URL doesn't match junk patterns.
    """
    eligible = []
    for page in pages:
        if page.get('status_code') != 200:
            continue
        if (page.get('word_count') or 0) < MIN_WORD_COUNT:
            continue
        body = page.get('body_text') or ''
        if not body.strip():
            continue
        url = page.get('url', '')
        parsed = urlparse(url)
        path = parsed.path.lower()
        query = parsed.query.lower()
        is_junk = False
        for pattern in JUNK_URL_PATTERNS:
            if '?' in pattern:
                if pattern.lstrip('?') in query:
                    is_junk = True
                    break
            elif pattern in path:
                is_junk = True
                break
        if is_junk:
            continue
        eligible.append(page)
    return eligible


def estimate_cost(eligible_pages, model='gpt-4o-mini'):
    """Estimate the cost of claim extraction."""
    total_words = sum(p.get('word_count', 0) for p in eligible_pages)
    estimated_input_tokens = int(total_words * 1.4)
    estimated_input_tokens += len(eligible_pages) * 200
    estimated_output_tokens = int(estimated_input_tokens * 0.3)
    pricing = {
        'gpt-4o-mini': {'input': 0.15, 'output': 0.60},
    }
    rates = pricing.get(model, pricing['gpt-4o-mini'])
    cost = (
        (estimated_input_tokens / 1_000_000) * rates['input'] +
        (estimated_output_tokens / 1_000_000) * rates['output']
    )
    return {
        'eligible_pages': len(eligible_pages),
        'estimated_input_tokens': estimated_input_tokens,
        'estimated_output_tokens': estimated_output_tokens,
        'estimated_cost': round(cost, 4),
        'model': model,
    }


def truncate_text(text, max_tokens=MAX_INPUT_TOKENS):
    """Truncate text to approximately max_tokens. Rough: 1 token ~ 4 chars."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _extract_claims_for_page(url, body_text, api_key, model='gpt-4o-mini'):
    """Extract claims from a single page using OpenAI API.
    Returns list of {"url": ..., "claim": ..., "source_text": ...} dicts.
    Retries up to 3 times with exponential backoff.
    """
    import openai

    client = openai.OpenAI(api_key=api_key)
    truncated = truncate_text(body_text)

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT},
                    {"role": "user", "content": truncated},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content.strip()
            parsed = json.loads(content)

            # Handle {"claims": [...]} format (forced by json_object mode)
            if isinstance(parsed, dict) and 'claims' in parsed:
                claims = parsed['claims']
            elif isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        claims = v
                        break
                else:
                    claims = []
            else:
                claims = []

            valid_claims = []
            for c in claims:
                if isinstance(c, dict) and 'claim' in c and 'source_text' in c:
                    valid_claims.append({
                        'url': url,
                        'claim': str(c['claim']),
                        'source_text': str(c['source_text']),
                    })
            return valid_claims

        except (json.JSONDecodeError, openai.APIError) as e:
            logger.warning(f"Claim extraction attempt {attempt+1}/3 failed for {url}: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue
        except Exception as e:
            logger.error(f"Unexpected error extracting claims for {url}: {e}")
            return []

    logger.error(f"All 3 attempts failed for {url}")
    return []


def extract_claims(crawl_id, pages, api_key, on_progress=None, model='gpt-4o-mini'):
    """Extract claims from all eligible pages using ThreadPoolExecutor.

    Args:
        crawl_id: The crawl ID for database storage
        pages: List of eligible page dicts with url, body_text
        api_key: OpenAI API key
        on_progress: Callback(processed, total, total_claims, failed)
        model: OpenAI model name

    Returns:
        dict with status, total_pages, processed, total_claims, failed, model
    """
    from src.crawl_db import save_claims_batch, delete_claims, update_claims_stats

    # Resume: skip pages that already have claims
    try:
        from src.crawl_db import get_db_connection
        conn = get_db_connection()
        already_done = set(
            row[0] for row in conn.execute(
                'SELECT DISTINCT url FROM page_claims WHERE crawl_id = ?', (crawl_id,)
            ).fetchall()
        )
        conn.close()
        if already_done:
            logger.info(f"Resuming claims: skipping {len(already_done)} already-extracted pages")
            pages = [p for p in pages if p['url'] not in already_done]
    except Exception:
        already_done = set()

    total = len(pages)
    processed = 0
    total_claims = len(already_done)
    failed = 0

    if total == 0:
        return {'status': 'completed', 'total_pages': len(already_done), 'processed': len(already_done),
                'total_claims': total_claims, 'failed': 0, 'model': model}

    update_claims_stats(crawl_id, {
        'status': 'running',
        'total_pages': total,
        'processed': 0,
        'total_claims': 0,
        'failed': 0,
        'model': model,
    })

    def process_page(page):
        return _extract_claims_for_page(
            page['url'], page.get('body_text', ''), api_key, model
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_page = {executor.submit(process_page, p): p for p in pages}

        for future in as_completed(future_to_page):
            page = future_to_page[future]

            try:
                claims = future.result()
                if claims:
                    save_claims_batch(crawl_id, claims)
                    total_claims += len(claims)
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Failed to process {page['url']}: {e}")
                failed += 1

            processed += 1

            if on_progress:
                on_progress(processed, total, total_claims, failed)

    final_status = 'completed' if failed == 0 else ('failed' if failed == total else 'completed')
    stats = {
        'status': final_status,
        'total_pages': total,
        'processed': processed,
        'total_claims': total_claims,
        'failed': failed,
        'model': model,
    }
    update_claims_stats(crawl_id, stats)

    return stats
