"""Tests for challenge vs hard-block classification."""
import pytest
from src.crawler import WebCrawler


@pytest.fixture
def crawler():
    c = WebCrawler.__new__(WebCrawler)
    return c


CLOUDFLARE_CHALLENGE_HTML = '''<!DOCTYPE html><html><head>
<title>Just a moment...</title></head><body>
<script>window._cf_chl_opt = {cType: 'managed'};</script>
<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>
</body></html>'''

CLOUDFLARE_WAF_HTML = '''<!DOCTYPE html><html><head>
<title>Attention Required! | Cloudflare</title></head><body>
<h1>Sorry, you have been blocked</h1>
<h2>You are unable to access roccommercecloud.com</h2>
</body></html>'''

VERCEL_CHALLENGE_HTML = '''<!DOCTYPE html><html><head>
<title>Vercel Security Checkpoint</title></head><body>
<p>We're verifying your browser</p>
</body></html>'''


class TestCloudflareChallenge:
    def test_403_with_challenge_html_is_challenge(self, crawler):
        result = {'status_code': 403, 'body_text': '', '_raw_html': CLOUDFLARE_CHALLENGE_HTML}
        assert crawler._classify_response(result) == 'challenge'

    def test_cf_chl_opt_signature_detected(self, crawler):
        result = {'status_code': 403, 'body_text': '', '_raw_html': '<script>_cf_chl_opt={}</script>'}
        assert crawler._classify_response(result) == 'challenge'

    def test_challenge_platform_script_detected(self, crawler):
        result = {'status_code': 200, 'body_text': '', '_raw_html': '<script src="/cdn-cgi/challenge-platform/foo"></script>'}
        assert crawler._classify_response(result) == 'challenge'


class TestCloudflareWAFBlock:
    def test_403_with_block_html_is_hard_block(self, crawler):
        result = {'status_code': 403, 'body_text': 'Sorry, you have been blocked', '_raw_html': CLOUDFLARE_WAF_HTML}
        assert crawler._classify_response(result) == 'hard_block'

    def test_access_denied_is_hard_block(self, crawler):
        result = {'status_code': 403, 'body_text': 'Access denied', '_raw_html': '<html><body>Access denied</body></html>'}
        assert crawler._classify_response(result) == 'hard_block'


class TestVercelChallenge:
    def test_429_with_vercel_checkpoint_is_challenge(self, crawler):
        result = {'status_code': 429, 'body_text': '', '_raw_html': VERCEL_CHALLENGE_HTML}
        assert crawler._classify_response(result) == 'challenge'

    def test_429_without_signatures_is_challenge(self, crawler):
        result = {'status_code': 429, 'body_text': 'rate limited', '_raw_html': ''}
        assert crawler._classify_response(result) == 'challenge'


class TestEdgeCases:
    def test_status_0_is_hard_block(self, crawler):
        result = {'status_code': 0, 'body_text': '', '_raw_html': ''}
        assert crawler._classify_response(result) == 'hard_block'

    def test_200_with_real_content_is_ok(self, crawler):
        result = {'status_code': 200, 'body_text': 'x' * 500, '_raw_html': '<html><body>' + 'x' * 500 + '</body></html>'}
        assert crawler._classify_response(result) == 'ok'

    def test_200_with_thin_body_is_challenge(self, crawler):
        result = {'status_code': 200, 'body_text': 'hi', '_raw_html': '<html><body>hi</body></html>'}
        assert crawler._classify_response(result) == 'challenge'

    def test_200_challenge_in_script_tags(self, crawler):
        result = {'status_code': 200, 'body_text': 'Enable JavaScript', '_raw_html': '<script>window._cf_chl_opt={}</script>'}
        assert crawler._classify_response(result) == 'challenge'

    def test_no_raw_html_falls_back_to_body_text(self, crawler):
        result = {'status_code': 403, 'body_text': 'sorry, you have been blocked'}
        assert crawler._classify_response(result) == 'hard_block'

    def test_403_no_signatures_is_hard_block(self, crawler):
        result = {'status_code': 403, 'body_text': 'forbidden', '_raw_html': '<html>forbidden</html>'}
        assert crawler._classify_response(result) == 'hard_block'
