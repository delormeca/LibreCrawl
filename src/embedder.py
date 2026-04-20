"""OpenAI embedding module for content vectorization mode."""

import struct
import time
import os
import logging

import tiktoken
from openai import OpenAI

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = 'text-embedding-3-large'
EMBEDDING_DIMENSIONS = 3072
MAX_TOKENS = 8000  # leave margin below 8191 limit
BATCH_SIZE = 100
MAX_RETRIES = 3


def validate_api_key(api_key=None):
    """Check that OPENAI_API_KEY is set and valid. Returns (ok, error_message)."""
    if not api_key:
        api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        return False, 'No OpenAI API key provided'
    try:
        client = OpenAI(api_key=api_key)
        client.embeddings.create(input='test', model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS)
        return True, None
    except Exception as e:
        return False, f'OpenAI API key validation failed: {str(e)}'


def build_embedding_input(title, h1, meta_description, body_text):
    """Concatenate page fields into a single embedding input string."""
    parts = [p for p in [title, h1, meta_description, body_text] if p]
    return ' | '.join(parts)


def truncate_to_tokens(text, max_tokens=MAX_TOKENS):
    """Truncate text to max_tokens using tiktoken cl100k_base encoding."""
    enc = tiktoken.get_encoding('cl100k_base')
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text, len(tokens)
    truncated = enc.decode(tokens[:max_tokens])
    return truncated, max_tokens


def embedding_to_bytes(embedding_list):
    """Convert a list of floats to bytes for BLOB storage."""
    return struct.pack(f'{len(embedding_list)}f', *embedding_list)


def bytes_to_embedding(blob):
    """Convert BLOB bytes back to a list of floats."""
    count = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f'{count}f', blob))


def embed_pages(pages, progress_callback=None, api_key=None):
    """Generate embeddings for a list of pages.

    Args:
        pages: List of dicts with keys: url, title, h1, meta_description, body_text, internal_links_out
        progress_callback: Optional callable(current, total, failed) for progress reporting
        api_key: Optional OpenAI API key (falls back to OPENAI_API_KEY env var)

    Returns:
        List of dicts ready for save_embeddings_batch(), plus summary stats.
    """
    if not api_key:
        api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    client = OpenAI(api_key=api_key)

    results = []
    failed = 0
    total = len(pages)

    # Prepare all inputs with truncation
    prepared = []
    for page in pages:
        embedding_input = build_embedding_input(
            page.get('title', ''),
            page.get('h1', ''),
            page.get('meta_description', ''),
            page.get('body_text', '')
        )
        if not embedding_input.strip():
            failed += 1
            continue
        truncated, token_count = truncate_to_tokens(embedding_input)
        prepared.append({
            **page,
            'embedding_input': truncated,
            'token_count': token_count,
        })

    # Batch embed
    for i in range(0, len(prepared), BATCH_SIZE):
        batch = prepared[i:i + BATCH_SIZE]
        texts = [p['embedding_input'] for p in batch]

        embedding_response = None
        for attempt in range(MAX_RETRIES):
            try:
                embedding_response = client.embeddings.create(
                    input=texts,
                    model=EMBEDDING_MODEL,
                    dimensions=EMBEDDING_DIMENSIONS
                )
                break
            except Exception as e:
                logger.warning(f'Embedding batch {i // BATCH_SIZE + 1} attempt {attempt + 1} failed: {e}')
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # exponential backoff: 1s, 2s, 4s
                else:
                    logger.error(f'Embedding batch {i // BATCH_SIZE + 1} failed after {MAX_RETRIES} retries')
                    failed += len(batch)

        if embedding_response:
            for j, embedding_data in enumerate(embedding_response.data):
                page_data = batch[j]
                results.append({
                    'url': page_data['url'],
                    'title': page_data.get('title'),
                    'h1': page_data.get('h1'),
                    'meta_description': page_data.get('meta_description'),
                    'body_text': page_data.get('body_text'),
                    'embedding_input': page_data['embedding_input'],
                    'embedding': embedding_to_bytes(embedding_data.embedding),
                    'internal_links_out': page_data.get('internal_links_out'),
                    'token_count': page_data['token_count'],
                    'model': EMBEDDING_MODEL,
                })

        if progress_callback:
            progress_callback(len(results) + failed, total, failed)

    return results, {'total': total, 'embedded': len(results), 'failed': failed}
