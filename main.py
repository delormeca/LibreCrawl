import threading
import time
import csv
import json
import xml.etree.ElementTree as ET
import uuid
import webbrowser
import argparse
import secrets
import string
import os
import zipfile
from io import StringIO, BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from flask_compress import Compress
from functools import wraps
from src.crawler import WebCrawler
from src.settings_manager import SettingsManager
from src.auth_db import init_db, create_user, authenticate_user, get_user_by_id, log_guest_crawl, get_guest_crawls_last_24h, verify_user, set_user_tier, create_verification_token, verify_token, get_user_by_email
from src.email_service import send_verification_email, send_welcome_email

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Parse command line arguments
parser = argparse.ArgumentParser(description='LibreCrawl - SEO Spider Tool')
parser.add_argument('--local', '-l', action='store_true',
                    help='Run in local mode (all users get admin tier, no rate limits)')
parser.add_argument('--disable-register', '-dr', action='store_true',
                    help='Disable new user registrations')
parser.add_argument('--disable-guest', '-dg', action='store_true',
                    help='Disable guest login')
parser.add_argument('--demo', '-dm', action='store_true',
                    help='Demo mode: 1.5GB memory limit per user, crawls auto-stop at limit')
args = parser.parse_args()

LOCAL_MODE = args.local
DISABLE_REGISTER = args.disable_register
DISABLE_GUEST = args.disable_guest or os.getenv('DISABLE_GUEST', '').lower() in ('true', '1', 'yes')
DEMO_MODE = args.demo or os.getenv('DEMO_MODE', '').lower() in ('true', '1', 'yes')

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')
app.secret_key = 'librecrawl-secret-key-change-in-production'  # TODO: Use environment variable in production

# Enable compression for all responses
Compress(app)

# Initialize database on startup
init_db()

# Embedding progress state
embed_progress = {'current': 0, 'total': 0, 'failed': 0, 'status': 'idle'}

# Claims extraction progress state
claims_progress = {'processed': 0, 'total': 0, 'total_claims': 0, 'failed': 0, 'status': 'idle'}

# User-provided OpenAI API key (overrides env var when set)
user_openai_key = None

# Load persisted OpenAI key from DB on startup
try:
    import sqlite3 as _sq, json as _js
    _conn = _sq.connect('data/users.db')
    _row = _conn.execute('SELECT settings_json FROM user_settings WHERE user_id=1').fetchone()
    if _row:
        _s = _js.loads(_row[0])
        if _s.get('openaiApiKey'):
            user_openai_key = _s['openaiApiKey']
            print(f"Loaded OpenAI API key from DB")
    _conn.close()
except Exception:
    pass

def generate_random_password(length=16):
    """Generate a random password with letters, digits, and symbols"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def auto_login_local_mode():
    """Auto-login for local mode - creates or logs into 'local' admin account"""
    import sqlite3
    try:
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if 'local' user exists
        cursor.execute('SELECT id, username, tier FROM users WHERE username = ?', ('local',))
        user = cursor.fetchone()

        if user:
            # User exists, just log them in
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['tier'] = 'admin'
            session.permanent = True
            print(f"Auto-logged in as existing 'local' user (ID: {user['id']})")
        else:
            # Create new local user with random password
            random_password = generate_random_password()
            from src.auth_db import hash_password
            password_hash = hash_password(random_password)

            cursor.execute('''
                INSERT INTO users (username, email, password_hash, verified, tier)
                VALUES (?, ?, ?, 1, 'admin')
            ''', ('local', 'local@localhost', password_hash))
            conn.commit()

            user_id = cursor.lastrowid

            # Log in the new user
            session['user_id'] = user_id
            session['username'] = 'local'
            session['tier'] = 'admin'
            session.permanent = True

            print(f"Created and auto-logged in as new 'local' admin user (ID: {user_id})")
            print(f"Generated password: {random_password}")

        conn.close()
        return True
    except Exception as e:
        print(f"Error in auto_login_local_mode: {e}")
        return False

if LOCAL_MODE:
    print("=" * 60)
    print("LOCAL MODE ENABLED")
    print("All users will have admin tier access")
    print("No rate limits or tier restrictions")
    print("Auto-login enabled with 'local' admin account")
    print("=" * 60)

if DISABLE_REGISTER:
    print("=" * 60)
    print("REGISTRATION DISABLED")
    print("New user registrations are not allowed")
    print("=" * 60)

if DISABLE_GUEST:
    print("=" * 60)
    print("GUEST MODE DISABLED")
    print("Guest login is not allowed")
    print("=" * 60)

if DEMO_MODE:
    print("=" * 60)
    print("DEMO MODE ENABLED")
    print("Memory limit: 1.5GB per user")
    print("Crawls will auto-stop when limit is reached")
    print("=" * 60)

def get_client_ip():
    """Get the real client IP address, checking Cloudflare headers first"""
    # Check Cloudflare header first
    if 'CF-Connecting-IP' in request.headers:
        return request.headers['CF-Connecting-IP']
    # Check other common proxy headers
    if 'X-Forwarded-For' in request.headers:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return request.headers['X-Forwarded-For'].split(',')[0].strip()
    if 'X-Real-IP' in request.headers:
        return request.headers['X-Real-IP']
    # Fall back to direct connection IP
    return request.remote_addr

def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # In local mode, auto-login if not already logged in
        if LOCAL_MODE and 'user_id' not in session:
            auto_login_local_mode()
        elif 'user_id' not in session:
            # Not in local mode and not logged in
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Multi-tenant crawler instances
crawler_instances = {}  # session_id -> {'crawler': WebCrawler, 'settings': SettingsManager, 'last_accessed': datetime}
instances_lock = threading.Lock()

def get_or_create_crawler():
    """Get or create a crawler instance for the current session"""
    # Get or create session ID
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())

    session_id = session['session_id']
    user_id = session.get('user_id')  # Get user_id from session
    tier = session.get('tier', 'guest')  # Get tier from session

    with instances_lock:
        # Check if crawler exists for this session
        if session_id not in crawler_instances:
            print(f"Creating new crawler instance for session: {session_id}, user: {user_id}, tier: {tier}")
            crawler_instances[session_id] = {
                'crawler': WebCrawler(),
                'settings': SettingsManager(session_id=session_id, user_id=user_id, tier=tier),  # Per-user settings
                'last_accessed': datetime.now()
            }
        else:
            # Update last accessed time
            crawler_instances[session_id]['last_accessed'] = datetime.now()

        return crawler_instances[session_id]['crawler']

def get_session_settings():
    """Get the settings manager for the current session"""
    # Get or create session ID
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())

    session_id = session['session_id']
    user_id = session.get('user_id')  # Get user_id from session
    tier = session.get('tier', 'guest')  # Get tier from session

    with instances_lock:
        # Create instance if it doesn't exist
        if session_id not in crawler_instances:
            print(f"Creating new settings instance for session: {session_id}, user: {user_id}, tier: {tier}")
            crawler_instances[session_id] = {
                'crawler': WebCrawler(),
                'settings': SettingsManager(session_id=session_id, user_id=user_id, tier=tier),
                'last_accessed': datetime.now()
            }
        else:
            # Update last accessed time
            crawler_instances[session_id]['last_accessed'] = datetime.now()

        return crawler_instances[session_id]['settings']

def cleanup_old_instances():
    """Remove crawler instances that haven't been accessed in 1 hour"""
    timeout = timedelta(hours=1)
    now = datetime.now()

    with instances_lock:
        sessions_to_remove = []
        for session_id, instance_data in crawler_instances.items():
            if now - instance_data['last_accessed'] > timeout:
                sessions_to_remove.append(session_id)

        for session_id in sessions_to_remove:
            print(f"Cleaning up crawler instance for session: {session_id}")
            # Stop any running crawls
            try:
                crawler_instances[session_id]['crawler'].stop_crawl()
            except:
                pass
            del crawler_instances[session_id]

        if sessions_to_remove:
            print(f"Cleaned up {len(sessions_to_remove)} inactive crawler instances")

def start_cleanup_thread():
    """Start background thread to cleanup old instances"""
    def cleanup_loop():
        while True:
            time.sleep(300)  # Check every 5 minutes
            try:
                cleanup_old_instances()
            except Exception as e:
                print(f"Error in cleanup thread: {e}")

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    print("Started crawler instance cleanup thread")

def generate_csv_export(urls, fields):
    """Generate CSV export content"""
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    for url_data in urls:
        row = {}
        for field in fields:
            value = url_data.get(field, '')

            # Handle complex data types for CSV
            if field == 'analytics' and isinstance(value, dict):
                analytics_list = []
                if value.get('gtag') or value.get('ga4_id'): analytics_list.append('GA4')
                if value.get('google_analytics'): analytics_list.append('GA')
                if value.get('gtm_id'): analytics_list.append('GTM')
                if value.get('facebook_pixel'): analytics_list.append('FB')
                if value.get('hotjar'): analytics_list.append('HJ')
                if value.get('mixpanel'): analytics_list.append('MP')
                row[field] = ', '.join(analytics_list)
            elif field == 'og_tags' and isinstance(value, dict):
                row[field] = f"{len(value)} tags" if value else ''
            elif field == 'twitter_tags' and isinstance(value, dict):
                row[field] = f"{len(value)} tags" if value else ''
            elif field == 'json_ld' and isinstance(value, list):
                row[field] = f"{len(value)} scripts" if value else ''
            elif field == 'images' and isinstance(value, list):
                row[field] = f"{len(value)} images" if value else ''
            elif field == 'internal_links' and isinstance(value, (int, float)):
                row[field] = f"{int(value)} internal links" if value else '0 internal links'
            elif field == 'external_links' and isinstance(value, (int, float)):
                row[field] = f"{int(value)} external links" if value else '0 external links'
            elif field == 'h2' and isinstance(value, list):
                row[field] = ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
            elif field == 'h3' and isinstance(value, list):
                row[field] = ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
            elif isinstance(value, (dict, list)):
                row[field] = str(value)
            else:
                row[field] = value

        writer.writerow(row)

    return output.getvalue()

def generate_json_export(urls, fields):
    """Generate JSON export content"""
    filtered_urls = []
    for url_data in urls:
        filtered_data = {}
        for field in fields:
            value = url_data.get(field, '')
            # Keep complex data structures intact in JSON
            filtered_data[field] = value
        filtered_urls.append(filtered_data)

    return json.dumps({
        'export_date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_urls': len(filtered_urls),
        'fields': fields,
        'data': filtered_urls
    }, indent=2, default=str)

def generate_xml_export(urls, fields):
    """Generate XML export content"""
    root = ET.Element('librecrawl_export')
    root.set('export_date', time.strftime('%Y-%m-%d %H:%M:%S'))
    root.set('total_urls', str(len(urls)))

    urls_element = ET.SubElement(root, 'urls')

    for url_data in urls:
        url_element = ET.SubElement(urls_element, 'url')
        for field in fields:
            field_element = ET.SubElement(url_element, field)
            field_element.text = str(url_data.get(field, ''))

    return ET.tostring(root, encoding='unicode')

def generate_links_csv_export(links):
    """Generate CSV export for links data"""
    output = StringIO()
    fieldnames = ['source_url', 'target_url', 'anchor_text', 'is_internal', 'target_domain', 'target_status', 'placement']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for link in links:
        row = {
            'source_url': link.get('source_url', ''),
            'target_url': link.get('target_url', ''),
            'anchor_text': link.get('anchor_text', ''),
            'is_internal': 'Yes' if link.get('is_internal') else 'No',
            'target_domain': link.get('target_domain', ''),
            'target_status': link.get('target_status', 'Not crawled'),
            'placement': link.get('placement', 'body')
        }
        writer.writerow(row)

    return output.getvalue()

def generate_links_json_export(links):
    """Generate JSON export for links data"""
    return json.dumps(links, indent=2)

def filter_issues_by_exclusion_patterns(issues, exclusion_patterns):
    """Filter issues based on exclusion patterns (applies current settings to loaded crawls)"""
    from fnmatch import fnmatch
    from urllib.parse import urlparse

    if not exclusion_patterns:
        return issues

    filtered_issues = []

    for issue in issues:
        url = issue.get('url', '')
        parsed = urlparse(url)
        path = parsed.path

        # Check if URL matches any exclusion pattern
        should_exclude = False
        for pattern in exclusion_patterns:
            if not pattern.strip() or pattern.strip().startswith('#'):
                continue

            if '*' in pattern:
                if fnmatch(path, pattern):
                    should_exclude = True
                    break
            elif path == pattern or path.startswith(pattern.rstrip('*')):
                should_exclude = True
                break

        if not should_exclude:
            filtered_issues.append(issue)

    return filtered_issues

def generate_issues_csv_export(issues):
    """Generate CSV export for issues data"""
    output = StringIO()
    fieldnames = ['url', 'type', 'category', 'issue', 'details']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for issue in issues:
        row = {
            'url': issue.get('url', ''),
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': issue.get('details', '')
        }
        writer.writerow(row)

    return output.getvalue()

def generate_issues_json_export(issues):
    """Generate JSON export for issues data"""
    # Group issues by URL for better organization
    issues_by_url = {}
    for issue in issues:
        url = issue.get('url', '')
        if url not in issues_by_url:
            issues_by_url[url] = []
        issues_by_url[url].append({
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': issue.get('details', '')
        })

    return json.dumps({
        'export_date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_issues': len(issues),
        'total_urls_with_issues': len(issues_by_url),
        'issues_by_url': issues_by_url,
        'all_issues': issues
    }, indent=2)

@app.route('/login')
def login_page():
    # In local mode, auto-login and redirect to index
    if LOCAL_MODE:
        auto_login_local_mode()
        return redirect(url_for('index'))
    # Redirect to app if already logged in
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html', registration_disabled=DISABLE_REGISTER, guest_disabled=DISABLE_GUEST)

@app.route('/register')
def register_page():
    # Redirect to app if already logged in
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('register.html', registration_disabled=DISABLE_REGISTER)

@app.route('/verify')
def verify_email():
    """Email verification endpoint"""
    token = request.args.get('token')

    if not token:
        return render_template('verification_result.html',
                             success=False,
                             message='Invalid verification link',
                             app_source='main')

    # Verify the token
    success, message, app_source, user_email = verify_token(token)

    # Send welcome email if successful
    if success and user_email:
        try:
            user = get_user_by_email(user_email)
            if user:
                send_welcome_email(user_email, user['username'], app_source or 'main')
        except Exception as e:
            print(f"Error sending welcome email: {e}")

    # Determine redirect URL based on app_source
    redirect_url = None
    if success:
        if app_source == 'workshop':
            redirect_url = os.getenv('WORKSHOP_APP_URL', 'https://workshop.librecrawl.com')
        else:
            redirect_url = url_for('login_page')

    return render_template('verification_result.html',
                         success=success,
                         message=message,
                         app_source=app_source or 'main',
                         redirect_url=redirect_url)

@app.route('/api/register', methods=['POST'])
def register():
    # Check if registration is disabled
    if DISABLE_REGISTER:
        return jsonify({'success': False, 'message': 'Registration is currently disabled'})

    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    success, message, user_id = create_user(username, email, password)

    # In local mode, auto-verify and set to admin tier
    if success and LOCAL_MODE:
        try:
            from src.auth_db import verify_user, set_user_tier
            # Get the user that was just created
            import sqlite3
            conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            conn.close()

            if user:
                verify_user(user['id'])
                set_user_tier(user['id'], 'admin')
                message = 'Account created and verified! You have admin access in local mode.'
        except Exception as e:
            print(f"Error during local mode auto-verification: {e}")
            # Don't fail the registration, just log the error
            # The account is still created successfully
    elif success:
        # Not in local mode - send verification email
        is_resend = (message == 'resend')
        try:
            # Create verification token
            token = create_verification_token(user_id, app_source='main')
            if token:
                # Send verification email
                email_success, email_message = send_verification_email(
                    email, username, token, app_source='main', is_resend=is_resend
                )
                if email_success:
                    if is_resend:
                        message = 'A verification email was already sent to this address. We\'ve updated your account details and sent a new verification link.'
                    else:
                        message = 'Registration successful! Please check your email to verify your account.'
                else:
                    message = 'Account created, but we could not send the verification email. Please contact support.'
                    print(f"Email error: {email_message}")
            else:
                message = 'Account created, but verification token generation failed. Please contact support.'
        except Exception as e:
            print(f"Error sending verification email: {e}")
            message = 'Account created, but we could not send the verification email. Please contact support.'

    return jsonify({'success': success, 'message': message})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    success, message, user_data = authenticate_user(username, password)

    if success:
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        # In local mode, always give admin tier
        session['tier'] = 'admin' if LOCAL_MODE else user_data['tier']
        session.permanent = True  # Remember login

    return jsonify({'success': success, 'message': message})

@app.route('/api/guest-login', methods=['POST'])
def guest_login():
    """Login as a guest user (no account required, limited to 3 crawls/24h)"""
    if DISABLE_GUEST:
        return jsonify({'success': False, 'message': 'Guest login is disabled'})

    # Create a guest session with no user_id but with tier='guest'
    # In local mode, guests also get admin tier
    session['user_id'] = None
    session['username'] = 'Guest'
    session['tier'] = 'admin' if LOCAL_MODE else 'guest'
    session.permanent = False  # Don't persist guest sessions

    return jsonify({'success': True, 'message': 'Logged in as guest'})

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/user/info')
@login_required
def user_info():
    """Get current user info including tier"""
    from src.auth_db import get_crawls_last_24h
    user_id = session.get('user_id')
    tier = session.get('tier', 'guest')
    username = session.get('username')

    # Get crawl count
    crawls_today = 0
    if tier == 'guest':
        # For guests, count from IP address
        client_ip = get_client_ip()
        crawls_today = get_guest_crawls_last_24h(client_ip)
    else:
        # For registered users, count from database
        crawls_today = get_crawls_last_24h(user_id)

    return jsonify({
        'success': True,
        'user': {
            'id': user_id,
            'username': username,
            'tier': tier,
            'crawls_today': crawls_today,
            'crawls_remaining': max(0, 3 - crawls_today) if tier == 'guest' else -1
        }
    })

@app.route('/')
def index():
    # In local mode, auto-login if not already logged in
    if LOCAL_MODE and 'user_id' not in session:
        auto_login_local_mode()
    elif 'user_id' not in session:
        # Not in local mode and not logged in, redirect to login
        return redirect(url_for('login_page'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """Crawl history dashboard"""
    return render_template('dashboard.html')

@app.route('/debug/memory')
@login_required
def debug_memory_page():
    """Debug page with nice UI for memory monitoring"""
    return render_template('debug_memory.html')

@app.route('/api/start_crawl', methods=['POST'])
@login_required
def start_crawl():
    from src.auth_db import get_crawls_last_24h, log_crawl_start

    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'success': False, 'error': 'URL is required'})

    user_id = session.get('user_id')
    session_id = session.get('session_id')

    # Clear any in-progress DB load
    for key in ['loading_crawl_id', 'db_load_url_offset', 'db_load_link_offset',
                 'db_load_issue_offset', 'db_load_total_urls', 'db_load_total_links',
                 'db_load_total_issues']:
        session.pop(key, None)

    tier = session.get('tier', 'guest')

    # Check guest limits (IP-based) - skip in local mode
    if tier == 'guest' and not LOCAL_MODE:
        client_ip = get_client_ip()
        crawls_from_ip = get_guest_crawls_last_24h(client_ip)

        if crawls_from_ip >= 3:
            return jsonify({
                'success': False,
                'error': 'Guest limit reached: 3 crawls per 24 hours from your IP address. Please register for unlimited crawls.'
            })

        # Log this guest crawl
        log_guest_crawl(client_ip)

    # Get or create crawler for this session
    crawler = get_or_create_crawler()
    settings_manager = get_session_settings()

    # Apply current settings to crawler before starting
    try:
        crawler_config = settings_manager.get_crawler_config()
        crawler.update_config(crawler_config)
    except Exception as e:
        print(f"Warning: Could not apply settings: {e}")

    # Content vectorization mode
    content_vectorization = data.get('contentVectorizationMode', False)
    crawler.set_content_vectorization_mode(content_vectorization)

    # Linkgraph mode (mutually exclusive with vectorization)
    linkgraph_mode = data.get('linkgraphMode', False)
    if linkgraph_mode and content_vectorization:
        return jsonify({
            'success': False,
            'error': 'linkgraph and vectorization modes are mutually exclusive'
        })
    crawler.set_linkgraph_mode(linkgraph_mode)

    # User-provided sitemap URLs
    sitemap_urls = data.get('sitemapUrls', [])
    if sitemap_urls:
        crawler.set_user_sitemap_urls(sitemap_urls)

    # Enforce demo mode limits
    if DEMO_MODE:
        crawler.config['demo_mode'] = True
        crawler.config['demo_memory_limit_bytes'] = int(1.5 * 1024 * 1024 * 1024)  # 1.5GB

    # Pass user_id and session_id for database persistence
    success, message = crawler.start_crawl(url, user_id=user_id, session_id=session_id)

    # Store crawl_id in session
    if success and crawler.crawl_id:
        session['current_crawl_id'] = crawler.crawl_id
        # Also log to old crawl_history for compatibility
        log_crawl_start(user_id, url)

    if content_vectorization and success:
        import threading as _threading

        cid = crawler.crawl_id
        embed_api_key = user_openai_key  # capture at request time

        def run_embedding_after_crawl(crawler_instance, crawl_id):
            global embed_progress
            # Wait for crawl to finish
            while crawler_instance.is_running:
                time.sleep(1)

            embed_progress.update({'current': 0, 'total': 0, 'failed': 0, 'status': 'embedding'})

            from src.embedder import embed_pages
            from src.crawl_db import save_embeddings_batch

            # Collect page data from crawl results (crawl_results is a list)
            pages = []
            for url_data in crawler_instance.crawl_results:
                if url_data.get('status_code') == 200 and url_data.get('body_text'):
                    pages.append({
                        'url': url_data['url'],
                        'title': url_data.get('title', ''),
                        'h1': url_data.get('h1', ''),
                        'meta_description': url_data.get('meta_description', ''),
                        'body_text': url_data.get('body_text', ''),
                        'internal_links_out': url_data.get('internal_links_out', '[]'),
                    })

            embed_progress.update({'total': len(pages)})

            def progress_cb(current, total, failed):
                embed_progress.update({'current': current, 'total': total, 'failed': failed, 'status': 'embedding'})

            results, stats = embed_pages(pages, progress_callback=progress_cb, api_key=embed_api_key)

            if results:
                save_embeddings_batch(crawl_id, results)

            embed_progress.update({'current': stats['embedded'], 'total': stats['total'], 'failed': stats['failed'], 'status': 'done'})

        _threading.Thread(target=run_embedding_after_crawl, args=(crawler, cid), daemon=True).start()

    return jsonify({'success': success, 'message': message, 'crawl_id': crawler.crawl_id})

@app.route('/api/stop_crawl', methods=['POST'])
@login_required
def stop_crawl():
    crawler = get_or_create_crawler()
    success, message = crawler.stop_crawl()
    return jsonify({'success': success, 'message': message})

@app.route('/api/crawl_status')
@login_required
def crawl_status():
    crawler = get_or_create_crawler()
    settings_manager = get_session_settings()

    # --- DB-loading branch: serve batches from database ---
    loading_crawl_id = session.get('loading_crawl_id')
    if loading_crawl_id:
        from src.crawl_db import load_crawled_urls, load_crawl_links, load_crawl_issues

        url_offset = session.get('db_load_url_offset', 0)
        link_offset = session.get('db_load_link_offset', 0)
        issue_offset = session.get('db_load_issue_offset', 0)
        total_urls = session.get('db_load_total_urls', 0)
        total_links = session.get('db_load_total_links', 0)
        total_issues = session.get('db_load_total_issues', 0)

        # Read next batch from DB
        new_urls = load_crawled_urls(loading_crawl_id, limit=200, offset=url_offset)
        new_links = load_crawl_links(loading_crawl_id, limit=5000, offset=link_offset)
        new_issues = load_crawl_issues(loading_crawl_id, limit=2000, offset=issue_offset)

        # Advance offsets
        session['db_load_url_offset'] = url_offset + len(new_urls)
        session['db_load_link_offset'] = link_offset + len(new_links)
        session['db_load_issue_offset'] = issue_offset + len(new_issues)

        # Inject into crawler for tab functionality (export, visualization, etc.)
        with crawler.results_lock:
            crawler.crawl_results.extend(new_urls)
            crawler.stats['crawled'] = len(crawler.crawl_results)
            crawler.stats['discovered'] = total_urls
            crawler.base_url = crawler.base_url or ''
        if crawler.link_manager:
            with crawler.link_manager.links_lock:
                crawler.link_manager.all_links.extend(new_links)
                for link in new_links:
                    link_key = f"{link['source_url']}|{link['target_url']}"
                    crawler.link_manager.links_set.add(link_key)
        if crawler.issue_detector:
            crawler.issue_detector.detected_issues.extend(new_issues)

        # Track in user memory
        for url_data in new_urls:
            crawler.user_memory.track_url(url_data)
        if new_links:
            crawler.user_memory.track_links(new_links)
        if new_issues:
            crawler.user_memory.track_issues(new_issues)

        # Calculate progress BEFORE potentially clearing session keys
        current_url_offset = session['db_load_url_offset']
        load_progress = (current_url_offset / max(total_urls, 1)) * 100

        # Check if loading is complete
        all_done = (current_url_offset >= total_urls
                    and session['db_load_link_offset'] >= total_links
                    and session['db_load_issue_offset'] >= total_issues)

        if all_done:
            # Run update_link_statuses once at the end
            if crawler.link_manager:
                crawler.link_manager.update_link_statuses(crawler.crawl_results)
            # Clear loading state
            for key in ['loading_crawl_id', 'db_load_url_offset', 'db_load_link_offset',
                         'db_load_issue_offset', 'db_load_total_urls', 'db_load_total_links',
                         'db_load_total_issues']:
                session.pop(key, None)
            load_status = 'completed'
            load_progress = 100.0
        else:
            load_status = 'loading'

        # Apply issue exclusion patterns
        filtered_issues = new_issues
        if new_issues:
            current_settings = settings_manager.get_settings()
            exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
            exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
            filtered_issues = filter_issues_by_exclusion_patterns(new_issues, exclusion_patterns)

        data_sizes = crawler.user_memory.get_stats()

        return jsonify({
            'status': load_status,
            'stats': {
                'crawled': len(crawler.crawl_results),
                'discovered': total_urls,
                'depth': 0,
                'speed': 0,
                'baseUrl': crawler.base_url
            },
            'urls': new_urls,
            'links': new_links,
            'issues': filtered_issues,
            'progress': load_progress,
            'is_running_pagespeed': False,
            'memory': {},
            'memory_data': data_sizes,
            'demo_stopped': False,
            'demo_mode': False
        })
    # --- End DB-loading branch ---

    # Check for incremental update parameters
    url_since = request.args.get('url_since', type=int)
    link_since = request.args.get('link_since', type=int)
    issue_since = request.args.get('issue_since', type=int)

    # Get full status data
    status_data = crawler.get_status()

    # Ensure baseUrl is in stats (needed for UI to work correctly)
    if crawler.base_url and 'stats' in status_data:
        status_data['stats']['baseUrl'] = crawler.base_url

    # Check if we need to force a full refresh (after loading from DB)
    force_full = session.pop('force_full_refresh', False)

    # If incremental parameters provided AND not forcing full refresh, slice the arrays
    if not force_full:
        if url_since is not None:
            status_data['urls'] = status_data.get('urls', [])[url_since:]
        if link_since is not None:
            status_data['links'] = status_data.get('links', [])[link_since:]
        if issue_since is not None:
            status_data['issues'] = status_data.get('issues', [])[issue_since:]

    # Apply current issue exclusion patterns to displayed issues
    issues = status_data.get('issues', [])
    if issues:
        current_settings = settings_manager.get_settings()
        exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
        exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
        filtered_issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)
        status_data['issues'] = filtered_issues

    return jsonify(status_data)

@app.route('/api/embed_status')
@login_required
def embed_status():
    # If in-memory status is idle, check DB for existing embeddings
    if embed_progress.get('status') == 'idle':
        crawl_id = session.get('current_crawl_id')
        if crawl_id:
            from src.crawl_db import get_embedding_count
            count = get_embedding_count(crawl_id)
            if count > 0:
                return jsonify({'current': count, 'total': count, 'failed': 0, 'status': 'done'})
    return jsonify(embed_progress)


@app.route('/api/embeddings_list')
@login_required
def embeddings_list():
    """Return list of embedded pages for the current crawl."""
    crawl_id = session.get('current_crawl_id')
    if not crawl_id:
        return jsonify({'pages': [], 'total': 0})
    from src.crawl_db import get_embeddings_for_crawl
    rows = get_embeddings_for_crawl(crawl_id)
    pages = []
    for row in rows:
        pages.append({
            'url': row[0],
            'title': row[1],
            'token_count': row[5],
        })
    return jsonify({'pages': pages, 'total': len(pages)})


@app.route('/api/set_openai_key', methods=['POST'])
@login_required
def set_openai_key():
    global user_openai_key
    data = request.get_json() or {}
    key = data.get('key', '').strip()
    if key:
        user_openai_key = key
        # Persist to user settings DB so it survives restarts
        try:
            import sqlite3, json
            conn = sqlite3.connect('data/users.db')
            row = conn.execute('SELECT settings_json FROM user_settings WHERE user_id=?', (session.get('user_id', 1),)).fetchone()
            if row:
                settings = json.loads(row[0])
                settings['openaiApiKey'] = key
                conn.execute('UPDATE user_settings SET settings_json=?, updated_at=datetime("now") WHERE user_id=?',
                             (json.dumps(settings), session.get('user_id', 1)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: could not persist OpenAI key: {e}")
        return jsonify({'success': True})
    else:
        user_openai_key = None
        return jsonify({'success': True, 'cleared': True})


@app.route('/api/check_openai_key')
@login_required
def check_openai_key():
    from src.embedder import validate_api_key
    key = user_openai_key or None
    ok, error = validate_api_key(api_key=key)
    return jsonify({'valid': ok, 'error': error})


# === Claims Extraction Endpoints ===

@app.route('/api/estimate_claims', methods=['POST'])
@login_required
def estimate_claims_endpoint():
    """Estimate cost of claim extraction for a crawl."""
    data = request.get_json(silent=True) or {}
    crawl_id = data.get('crawl_id')

    crawler = get_or_create_crawler()

    if crawl_id:
        from src.crawl_db import load_crawled_urls
        all_pages = load_crawled_urls(crawl_id)
    else:
        all_pages = list(crawler.crawl_results)
        crawl_id = session.get('current_crawl_id')

    if not all_pages:
        return jsonify({'error': 'No crawl data available', 'success': False}), 400

    from src.core.claim_extractor import filter_eligible_pages, estimate_cost
    eligible = filter_eligible_pages(all_pages)
    cost_info = estimate_cost(eligible)
    cost_info['filtered_out'] = len(all_pages) - len(eligible)
    cost_info['crawl_id'] = crawl_id
    cost_info['success'] = True

    return jsonify(cost_info)


@app.route('/api/extract_claims', methods=['POST'])
@login_required
def extract_claims_endpoint():
    """Start background claim extraction for a crawl."""
    global claims_progress

    if not user_openai_key:
        return jsonify({'error': 'OpenAI API key not set. Use /api/set_openai_key first.', 'success': False}), 400

    if claims_progress.get('status') == 'running':
        return jsonify({'error': 'Claim extraction already in progress', 'success': False}), 409

    data = request.get_json(silent=True) or {}
    crawl_id = data.get('crawl_id') or session.get('current_crawl_id')

    crawler = get_or_create_crawler()

    if crawl_id:
        from src.crawl_db import load_crawled_urls
        all_pages = load_crawled_urls(crawl_id)
    else:
        all_pages = list(crawler.crawl_results)

    if not all_pages:
        return jsonify({'error': 'No crawl data available', 'success': False}), 400

    from src.core.claim_extractor import filter_eligible_pages, estimate_cost, extract_claims
    eligible = filter_eligible_pages(all_pages)

    if not eligible:
        return jsonify({'error': 'No eligible pages for claim extraction', 'success': False}), 400

    cost_info = estimate_cost(eligible)

    claims_progress = {
        'processed': 0, 'total': len(eligible), 'total_claims': 0,
        'failed': 0, 'status': 'running', 'error': None,
    }

    api_key = user_openai_key

    def run_extraction():
        global claims_progress
        try:
            def progress_cb(processed, total, total_claims, failed):
                claims_progress.update({
                    'processed': processed, 'total': total,
                    'total_claims': total_claims, 'failed': failed,
                    'status': 'running',
                })

            stats = extract_claims(crawl_id, eligible, api_key, on_progress=progress_cb)
            claims_progress.update({
                'processed': stats['processed'], 'total': stats['total_pages'],
                'total_claims': stats['total_claims'], 'failed': stats['failed'],
                'status': stats['status'], 'error': None,
            })
        except Exception as e:
            logger.error(f"Claim extraction failed: {e}")
            claims_progress.update({'status': 'failed', 'error': str(e)})

    import threading as _threading
    _threading.Thread(target=run_extraction, daemon=True).start()

    return jsonify({
        'status': 'started',
        'eligible_pages': len(eligible),
        'estimated_cost': cost_info['estimated_cost'],
        'crawl_id': crawl_id,
        'success': True,
    })


@app.route('/api/claims_status', methods=['GET'])
@login_required
def claims_status_endpoint():
    """Get current claim extraction progress."""
    crawl_id = request.args.get('crawl_id', type=int)

    if claims_progress.get('status') == 'running':
        return jsonify({**claims_progress, 'success': True})

    if crawl_id:
        from src.crawl_db import get_claims_stats, count_claims
        stats = get_claims_stats(crawl_id)
        if stats:
            stats['success'] = True
            return jsonify(stats)

    return jsonify({**claims_progress, 'success': True})


@app.route('/api/claims_list', methods=['GET'])
@login_required
def claims_list_endpoint():
    """Get all claims for a crawl."""
    crawl_id = request.args.get('crawl_id', type=int) or session.get('current_crawl_id')

    if not crawl_id:
        return jsonify({'error': 'No crawl_id provided', 'success': False}), 400

    from src.crawl_db import load_claims, count_claims
    claims = load_claims(crawl_id)
    total = count_claims(crawl_id)

    return jsonify({'claims': claims, 'total': total, 'crawl_id': crawl_id, 'success': True})


@app.route('/api/visualization_data')
@login_required
def visualization_data():
    """Get graph data for site structure visualization"""
    try:
        crawler = get_or_create_crawler()
        status_data = crawler.get_status()

        # Get URLs from the status data
        crawled_pages = status_data.get('urls', [])
        all_links = status_data.get('links', [])

        # Build nodes and edges for the graph
        nodes = []
        edges = []
        url_to_id = {}

        # Create nodes from crawled pages (limit to prevent lag)
        max_nodes = 500  # Optimization: limit nodes for performance
        pages_to_visualize = crawled_pages[:max_nodes]

        for idx, page in enumerate(pages_to_visualize):
            url = page.get('url', '')
            status_code = page.get('status_code', 0)

            # Assign color based on status code
            if 200 <= status_code < 300:
                color = '#10b981'  # Green for 2xx
            elif 300 <= status_code < 400:
                color = '#3b82f6'  # Blue for 3xx
            elif 400 <= status_code < 500:
                color = '#f59e0b'  # Orange for 4xx
            elif 500 <= status_code < 600:
                color = '#ef4444'  # Red for 5xx
            else:
                color = '#6b7280'  # Gray for other

            # Create node
            node = {
                'data': {
                    'id': f'node-{idx}',
                    'label': url.split('/')[-1] or url.split('//')[-1],  # Use last path segment or domain
                    'url': url,
                    'status_code': status_code,
                    'title': page.get('title', ''),
                    'color': color,
                    'size': 30 if idx == 0 else 20,  # Make root node larger
                    'depth': page.get('depth', 0)
                }
            }
            nodes.append(node)
            url_to_id[url] = f'node-{idx}'

        # Create edges from links data
        # Links are stored as: {'source_url': url, 'target_url': url, 'is_internal': bool, ...}
        edges_set = set()  # Use set to avoid duplicate edges
        for link in all_links:
            if link.get('is_internal'):  # Only use internal links
                source_url = link.get('source_url', '')
                target_url = link.get('target_url', '')

                source_id = url_to_id.get(source_url)
                target_id = url_to_id.get(target_url)

                if source_id and target_id and source_id != target_id:
                    edge_key = f'{source_id}-{target_id}'
                    if edge_key not in edges_set:
                        edges_set.add(edge_key)
                        edge = {
                            'data': {
                                'id': f'edge-{edge_key}',
                                'source': source_id,
                                'target': target_id
                            }
                        }
                        edges.append(edge)

        return jsonify({
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'total_pages': len(crawled_pages),
            'visualized_pages': len(nodes),
            'truncated': len(crawled_pages) > max_nodes
        })

    except Exception as e:
        print(f"Error generating visualization data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'nodes': [],
            'edges': []
        })

@app.route('/api/debug/memory')
@login_required
def debug_memory():
    """Debug endpoint showing memory stats for all active crawler instances"""
    with instances_lock:
        memory_stats = {
            'total_instances': len(crawler_instances),
            'instances': []
        }

        for session_id, instance_data in crawler_instances.items():
            crawler = instance_data['crawler']
            stats = crawler.memory_monitor.get_stats()

            memory_stats['instances'].append({
                'session_id': session_id[:8] + '...',  # Truncate for privacy
                'last_accessed': instance_data['last_accessed'].isoformat(),
                'urls_crawled': len(crawler.crawl_results),
                'memory': stats,
                'data_sizes': crawler.user_memory.get_stats()
            })

        return jsonify(memory_stats)

@app.route('/api/debug/memory/profile')
@login_required
def debug_memory_profile():
    """Detailed memory profiling - what's actually using the RAM"""
    from src.core.memory_profiler import MemoryProfiler

    with instances_lock:
        profiles = []

        for session_id, instance_data in crawler_instances.items():
            crawler = instance_data['crawler']

            # Get object breakdown
            breakdown = MemoryProfiler.get_object_memory_breakdown()

            profiles.append({
                'session_id': session_id[:8] + '...',
                'urls_crawled': len(crawler.crawl_results),
                'object_breakdown': breakdown,
                'data_sizes': crawler.user_memory.get_stats()
            })

        return jsonify({
            'total_instances': len(crawler_instances),
            'profiles': profiles
        })

@app.route('/api/filter_issues', methods=['POST'])
@login_required
def filter_issues():
    try:
        data = request.get_json()
        issues = data.get('issues', [])
        settings_manager = get_session_settings()

        # Get current exclusion patterns
        current_settings = settings_manager.get_settings()
        exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
        exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]

        # Filter issues
        filtered_issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

        return jsonify({'success': True, 'issues': filtered_issues})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/get_settings')
@login_required
def get_settings():
    try:
        settings_manager = get_session_settings()
        settings = settings_manager.get_settings()
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/save_settings', methods=['POST'])
@login_required
def save_settings():
    try:
        data = request.get_json()
        settings_manager = get_session_settings()
        success, message = settings_manager.save_settings(data)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/reset_settings', methods=['POST'])
@login_required
def reset_settings():
    try:
        settings_manager = get_session_settings()
        success, message = settings_manager.reset_settings()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/update_crawler_settings', methods=['POST'])
@login_required
def update_crawler_settings():
    try:
        crawler = get_or_create_crawler()
        settings_manager = get_session_settings()
        # Get current settings and update crawler configuration
        crawler_config = settings_manager.get_crawler_config()
        crawler.update_config(crawler_config)
        return jsonify({'success': True, 'message': 'Crawler settings updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/pause_crawl', methods=['POST'])
@login_required
def pause_crawl():
    try:
        crawler = get_or_create_crawler()
        success, message = crawler.pause_crawl()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/resume_crawl', methods=['POST'])
@login_required
def resume_crawl():
    try:
        crawler = get_or_create_crawler()
        success, message = crawler.resume_crawl()
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/list')
@login_required
def list_crawls():
    """Get all crawls for current user"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import get_user_crawls, get_crawl_count

        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        status_filter = request.args.get('status')

        crawls = get_user_crawls(user_id, limit=limit, offset=offset, status_filter=status_filter)
        total_count = get_crawl_count(user_id)

        return jsonify({
            'success': True,
            'crawls': crawls,
            'total': total_count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/<int:crawl_id>')
@login_required
def get_crawl(crawl_id):
    """Get complete crawl data by ID"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import get_crawl_by_id, load_crawled_urls, load_crawl_links, load_crawl_issues

        # Get crawl metadata
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        # Check ownership (guests have user_id = None)
        if user_id and crawl.get('user_id') != user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        # Load all data
        urls = load_crawled_urls(crawl_id)
        links = load_crawl_links(crawl_id)
        issues = load_crawl_issues(crawl_id)

        return jsonify({
            'success': True,
            'crawl': crawl,
            'urls': urls,
            'links': links,
            'issues': issues
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/load', methods=['POST'])
@login_required
def load_crawl_into_session(crawl_id):
    """Load a historical crawl into the current session via streamed batches"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import (get_crawl_by_id, count_crawled_urls,
                                   count_crawl_links, count_crawl_issues)

        # Get crawl metadata
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        # Check ownership
        if user_id and crawl.get('user_id') != user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        # Get current crawler instance
        crawler = get_or_create_crawler()

        # Stop any running crawl
        if crawler.is_running:
            crawler.stop_crawl()

        # Clear any previous load state
        for key in ['loading_crawl_id', 'db_load_url_offset', 'db_load_link_offset',
                     'db_load_issue_offset', 'db_load_total_urls', 'db_load_total_links',
                     'db_load_total_issues']:
            session.pop(key, None)

        # Set base URL/domain BEFORE initializing components (LinkManager needs base_domain)
        crawler.base_url = crawl['base_url']
        crawler.base_domain = crawl['base_domain']

        # Initialize components if needed
        if not crawler.link_manager or not crawler.issue_detector:
            crawler._initialize_components()

        # Reset crawler state for fresh load
        with crawler.results_lock:
            crawler.crawl_results = []
            crawler.stats['crawled'] = 0
            crawler.stats['discovered'] = 0
        if crawler.link_manager:
            crawler.link_manager.all_links = []
            crawler.link_manager.links_set.clear()
        if crawler.issue_detector:
            crawler.issue_detector.detected_issues = []
        crawler.user_memory.reset()
        crawler._demo_limit_reached = False

        # Get total counts for progress tracking
        total_urls = count_crawled_urls(crawl_id)
        total_links = count_crawl_links(crawl_id)
        total_issues = count_crawl_issues(crawl_id)

        # Store loading state in session
        session['loading_crawl_id'] = crawl_id
        session['current_crawl_id'] = crawl_id
        session['db_load_url_offset'] = 0
        session['db_load_link_offset'] = 0
        session['db_load_issue_offset'] = 0
        session['db_load_total_urls'] = total_urls
        session['db_load_total_links'] = total_links
        session['db_load_total_issues'] = total_issues

        return jsonify({
            'success': True,
            'status': 'loading',
            'total_urls': total_urls,
            'total_links': total_links,
            'total_issues': total_issues
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawls/<int:crawl_id>/resume', methods=['POST'])
@login_required
def resume_crawl_endpoint(crawl_id):
    """Resume an interrupted crawl"""
    try:
        user_id = session.get('user_id')
        session_id = session.get('session_id')

        # Get crawler for this session
        crawler = get_or_create_crawler()

        # Enforce demo mode limits on resumed crawls
        if DEMO_MODE:
            crawler.config['demo_mode'] = True
            crawler.config['demo_memory_limit_bytes'] = int(1.5 * 1024 * 1024 * 1024)

        # Resume from database
        success, message = crawler.resume_from_database(crawl_id, user_id=user_id, session_id=session_id)

        if success:
            session['current_crawl_id'] = crawl_id

        return jsonify({'success': success, 'message': message})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/<int:crawl_id>/delete', methods=['DELETE'])
@login_required
def delete_crawl_endpoint(crawl_id):
    """Delete a crawl and all associated data"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import delete_crawl, get_crawl_by_id

        # Verify ownership
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        if user_id and crawl.get('user_id') != user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        from src.crawl_db import delete_claims
        delete_claims(crawl_id)

        success = delete_crawl(crawl_id)
        return jsonify({'success': success, 'message': 'Crawl deleted successfully' if success else 'Failed to delete crawl'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/<int:crawl_id>/archive', methods=['POST'])
@login_required
def archive_crawl(crawl_id):
    """Archive crawl (mark as archived but keep data)"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import set_crawl_status, get_crawl_by_id

        # Verify ownership
        crawl = get_crawl_by_id(crawl_id)
        if not crawl:
            return jsonify({'success': False, 'error': 'Crawl not found'}), 404

        if user_id and crawl.get('user_id') != user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        success = set_crawl_status(crawl_id, 'archived')
        return jsonify({'success': success, 'message': 'Crawl archived successfully' if success else 'Failed to archive crawl'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crawls/stats')
@login_required
def crawl_stats():
    """Get statistics about user's crawls"""
    try:
        user_id = session.get('user_id')
        from src.crawl_db import get_crawl_count, get_database_size_mb
        import sqlite3

        # Get counts by status
        conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM crawls
            WHERE user_id = ?
            GROUP BY status
        ''', (user_id,))

        status_counts = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        return jsonify({
            'success': True,
            'total_crawls': get_crawl_count(user_id),
            'by_status': status_counts,
            'database_size_mb': get_database_size_mb()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def generate_linkgraph_json_export(crawl_id):
    """Generate self-contained linkgraph JSON for internal linking analysis.

    Works on any crawl. Linkgraph-mode crawls get rich sections + link context.
    Regular crawls get degraded output (body_text + basic links, no sections).
    """
    from src.crawl_db import (
        get_crawl_by_id, load_crawled_urls, load_crawl_links, load_crawl_sections
    )

    crawl = get_crawl_by_id(crawl_id)
    if not crawl:
        return None

    urls = load_crawled_urls(crawl_id)
    links = load_crawl_links(crawl_id)
    sections_rows = load_crawl_sections(crawl_id)

    # Group sections by URL
    sections_by_url = {}
    for s in sections_rows:
        url = s['url']
        if url not in sections_by_url:
            sections_by_url[url] = []
        sections_by_url[url].append({
            'heading': s.get('heading', ''),
            'heading_level': s.get('heading_level', 2),
            'text': s.get('text', ''),
            'word_count': s.get('word_count', 0),
            'position': s.get('position', 0),
        })

    # Group outgoing links by source URL
    outgoing_by_source = {}
    for link in links:
        source = link['source_url']
        if source not in outgoing_by_source:
            outgoing_by_source[source] = {'internal': [], 'external': []}

        attributes = None
        if link.get('attributes'):
            try:
                attributes = json.loads(link['attributes']) if isinstance(link['attributes'], str) else link['attributes']
            except (json.JSONDecodeError, TypeError):
                attributes = None

        link_entry = {
            'target_url': link['target_url'],
            'anchor_text': link.get('anchor_text', ''),
            'placement': link.get('placement', 'body'),
            'placement_detail': link.get('placement_detail') or link.get('placement', 'body'),
            'context': link.get('context') or None,
            'parent_heading': link.get('parent_heading') or None,
            'section_position': link.get('section_position'),
            'parent_tag': link.get('parent_tag') or None,
            'is_image_link': bool(link.get('is_image_link')),
            'is_nofollow': bool(link.get('is_nofollow')),
            'attributes': attributes,
        }

        bucket = 'internal' if link.get('is_internal') else 'external'
        outgoing_by_source[source][bucket].append(link_entry)

    # Build incoming links (inverse of internal outgoing)
    incoming_by_target = {}
    for link in links:
        if link.get('is_internal'):
            target = link['target_url']
            if target not in incoming_by_target:
                incoming_by_target[target] = []
            incoming_by_target[target].append({
                'source_url': link['source_url'],
                'anchor_text': link.get('anchor_text', ''),
                'placement': link.get('placement', 'body'),
                'placement_detail': link.get('placement_detail') or link.get('placement', 'body'),
            })

    # Build pages dict
    pages = {}
    html_urls = set()
    total_internal = 0
    total_external = 0

    for url_data in urls:
        url = url_data['url']
        content_type = url_data.get('content_type', '')

        if 'text/html' not in content_type:
            continue

        html_urls.add(url)
        outgoing = outgoing_by_source.get(url, {'internal': [], 'external': []})
        total_internal += len(outgoing['internal'])
        total_external += len(outgoing['external'])

        pages[url] = {
            'url': url,
            'status_code': url_data.get('status_code'),
            'content_type': content_type,
            'title': url_data.get('title', ''),
            'meta_description': url_data.get('meta_description', ''),
            'h1': url_data.get('h1', ''),
            'word_count': url_data.get('word_count', 0),
            'lang': url_data.get('lang', ''),
            'canonical_url': url_data.get('canonical_url', ''),
            'content': {
                'full_text': url_data.get('body_text', ''),
                'sections': sections_by_url.get(url, []),
            },
            'outgoing_links': outgoing,
            'incoming_links': incoming_by_target.get(url, []),
        }

    # Detect orphan pages (HTML pages with zero incoming internal links)
    orphan_pages = [url for url in html_urls if url not in incoming_by_target]

    # Collect non-HTML targets
    non_html_targets = []
    non_html_seen = set()
    for link in links:
        target = link['target_url']
        if link.get('is_internal') and target not in html_urls and target not in non_html_seen:
            non_html_seen.add(target)
            linked_from = [
                {'source_url': l['source_url'], 'anchor_text': l.get('anchor_text', '')}
                for l in links
                if l['target_url'] == target and l.get('is_internal')
            ]
            target_ct = ''
            for u in urls:
                if u['url'] == target:
                    target_ct = u.get('content_type', '')
                    break
            non_html_targets.append({
                'url': target,
                'content_type': target_ct,
                'linked_from': linked_from,
            })

    crawl_mode = crawl.get('crawl_mode', 'standard')
    mode_label = 'linkgraph' if crawl_mode == 'linkgraph' else 'regular'
    base_domain = crawl.get('base_domain', '')

    result = {
        'meta': {
            'domain': base_domain,
            'crawl_id': str(crawl_id),
            'crawl_date': crawl.get('started_at', ''),
            'mode': mode_label,
            'total_pages': len(pages),
            'total_internal_links': total_internal,
            'total_external_links': total_external,
        },
        'pages': pages,
        'orphan_pages': sorted(orphan_pages),
        'non_html_targets': non_html_targets,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def generate_claims_json_export(crawl_id, crawl_results):
    """Generate claims export as JSON, grouped by page."""
    from urllib.parse import urlparse
    from src.crawl_db import load_claims

    claims = load_claims(crawl_id)

    if not claims:
        return json.dumps({'export_date': datetime.now().strftime('%Y-%m-%d'), 'total_claims': 0, 'pages': []})

    # Build URL->metadata lookup from crawl results
    url_meta = {}
    results = crawl_results if isinstance(crawl_results, list) else list(crawl_results.values())
    for r in results:
        url = r.get('url', '')
        parsed = urlparse(url)
        url_meta[url] = {
            'title': r.get('title', ''),
            'h1': r.get('h1', ''),
            'slug': parsed.path,
            'lang': r.get('lang', ''),
            'word_count': r.get('word_count', 0),
        }

    base_domain = ''
    if results:
        parsed = urlparse(results[0].get('url', ''))
        base_domain = parsed.netloc

    export = {
        'export_date': datetime.now().strftime('%Y-%m-%d'),
        'domain': base_domain,
        'total_claims': len(claims),
    }

    # Group claims by URL
    pages = {}
    for c in claims:
        url = c['url']
        if url not in pages:
            meta = url_meta.get(url, {})
            pages[url] = {
                'url': url,
                'slug': meta.get('slug', ''),
                'title': meta.get('title', ''),
                'h1': meta.get('h1', ''),
                'lang': meta.get('lang', ''),
                'claims': [],
            }
        pages[url]['claims'].append({
            'claim': c['claim'],
            'source_text': c['source_text'],
        })

    export['total_pages'] = len(pages)
    export['pages'] = list(pages.values())

    return json.dumps(export, ensure_ascii=False, indent=2)


def generate_claims_csv_export(crawl_id, crawl_results):
    """Generate claims export as CSV. One row per claim with page metadata."""
    import csv
    import io
    from urllib.parse import urlparse
    from src.crawl_db import load_claims

    claims = load_claims(crawl_id)

    # Build URL->metadata lookup
    url_meta = {}
    results = crawl_results if isinstance(crawl_results, list) else list(crawl_results.values())
    for r in results:
        url = r.get('url', '')
        parsed = urlparse(url)
        url_meta[url] = {
            'title': r.get('title', ''),
            'h1': r.get('h1', ''),
            'slug': parsed.path,
            'lang': r.get('lang', ''),
            'word_count': r.get('word_count', 0),
        }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['url', 'slug', 'page_title', 'claim', 'source_text', 'page_word_count', 'page_lang', 'h1'])

    for c in claims:
        meta = url_meta.get(c['url'], {})
        writer.writerow([
            c['url'], meta.get('slug', ''), meta.get('title', ''),
            c['claim'], c['source_text'],
            meta.get('word_count', ''), meta.get('lang', ''), meta.get('h1', ''),
        ])

    return output.getvalue()


@app.route('/api/export_data', methods=['POST'])
@login_required
def export_data():
    try:
        data = request.get_json()
        export_format = data.get('format', 'csv')
        export_fields = data.get('fields', ['url', 'status_code', 'title'])
        local_data = data.get('localData', {})

        # Special case: linkgraph-json export (requires crawl_id, reads from DB)
        if export_format == 'linkgraph-json':
            crawl_id = data.get('crawlId') or session.get('current_crawl_id')
            if not crawl_id:
                return jsonify({'success': False, 'error': 'No crawl ID for linkgraph export'})

            content = generate_linkgraph_json_export(crawl_id)
            if not content:
                return jsonify({'success': False, 'error': 'Could not generate linkgraph export'})

            return jsonify({
                'success': True,
                'content': content,
                'mimetype': 'application/json',
                'filename': f'{data.get("baseUrl", "export").replace("https://","").replace("http://","").replace("www.","").rstrip("/")}_{datetime.now().strftime("%Y-%m-%d")}_linkgraph.json'
            })

        # Use local data if provided (from loaded crawl), otherwise get from crawler
        if local_data and local_data.get('urls'):
            urls = local_data.get('urls', [])
            links = local_data.get('links', [])
            issues = local_data.get('issues', [])
        else:
            # Get current crawl results
            crawler = get_or_create_crawler()
            crawl_data = crawler.get_status()
            urls = crawl_data.get('urls', [])
            links = crawl_data.get('links', [])
            issues = crawl_data.get('issues', [])

        # Deduplicate URLs by URL field (keep last occurrence — stealth retry has better data)
        seen = {}
        for u in urls:
            seen[u.get('url', '')] = u
        urls = list(seen.values())

        if not urls:
            return jsonify({'success': False, 'error': 'No data to export'})

        # Build clean filename prefix: domain_YYYY-MM-DD
        try:
            from urllib.parse import urlparse as _urlparse
            _first_url = urls[0].get('url', '') if urls else ''
            _domain = _urlparse(_first_url).hostname or 'export'
            _domain = _domain.replace('www.', '')
        except Exception:
            _domain = 'export'
        _date = datetime.now().strftime('%Y-%m-%d')
        _prefix = f'{_domain}_{_date}'

        # Update link statuses from crawled URLs (fixes missing status codes in exports)
        if links and urls:
            status_lookup = {url_data['url']: url_data.get('status_code') for url_data in urls}
            for link in links:
                target_url = link.get('target_url')
                if target_url in status_lookup:
                    link['target_status'] = status_lookup[target_url]

        # Apply current issue exclusion patterns (works for loaded crawls too)
        if issues:
            settings_manager = get_session_settings()
            current_settings = settings_manager.get_settings()
            exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
            exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
            issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)
            print(f"DEBUG: After exclusion filter, {len(issues)} issues remain")

        # Collect files to export based on special field selections
        files_to_export = []

        # Check for special export fields and prepare them as separate files
        has_issues_export = 'issues_detected' in export_fields
        has_links_export = 'links_detailed' in export_fields

        # Remove special fields from regular export fields
        regular_fields = [f for f in export_fields if f not in ['issues_detected', 'links_detailed']]

        # Debug logging
        print(f"DEBUG: export_fields = {export_fields}")
        print(f"DEBUG: has_issues_export = {has_issues_export}")
        print(f"DEBUG: has_links_export = {has_links_export}")
        print(f"DEBUG: regular_fields = {regular_fields}")
        print(f"DEBUG: len(urls) = {len(urls)}")
        print(f"DEBUG: len(links) = {len(links)}")
        print(f"DEBUG: len(issues) = {len(issues)}")

        # Generate issues export if requested
        if has_issues_export:
            if export_format == 'csv':
                issues_content = generate_issues_csv_export(issues)
                issues_mimetype = 'text/csv'
                issues_filename = f'{_prefix}_issues.csv'
            elif export_format == 'json':
                issues_content = generate_issues_json_export(issues)
                issues_mimetype = 'application/json'
                issues_filename = f'{_prefix}_issues.json'
            else:
                issues_content = generate_issues_csv_export(issues)
                issues_mimetype = 'text/csv'
                issues_filename = f'{_prefix}_issues.csv'

            files_to_export.append({
                'content': issues_content,
                'mimetype': issues_mimetype,
                'filename': issues_filename
            })

        # Generate links export if requested
        if has_links_export:
            if export_format == 'csv':
                links_content = generate_links_csv_export(links)
                links_mimetype = 'text/csv'
                links_filename = f'{_prefix}_links.csv'
            elif export_format == 'json':
                links_content = generate_links_json_export(links)
                links_mimetype = 'application/json'
                links_filename = f'{_prefix}_links.json'
            else:
                links_content = generate_links_csv_export(links)
                links_mimetype = 'text/csv'
                links_filename = f'{_prefix}_links.csv'

            files_to_export.append({
                'content': links_content,
                'mimetype': links_mimetype,
                'filename': links_filename
            })

        # Generate regular export if there are regular fields
        if regular_fields:
            if export_format == 'csv':
                regular_content = generate_csv_export(urls, regular_fields)
                regular_mimetype = 'text/csv'
                regular_filename = f'{_prefix}_crawl.csv'
            elif export_format == 'json':
                regular_content = generate_json_export(urls, regular_fields)
                regular_mimetype = 'application/json'
                regular_filename = f'{_prefix}_crawl.json'
            elif export_format == 'xml':
                regular_content = generate_xml_export(urls, regular_fields)
                regular_mimetype = 'application/xml'
                regular_filename = f'{_prefix}_crawl.xml'
            else:
                return jsonify({'success': False, 'error': 'Unsupported export format'})

            files_to_export.append({
                'content': regular_content,
                'mimetype': regular_mimetype,
                'filename': regular_filename
            })

        # Handle special case where only special fields are selected but no data
        if not files_to_export:
            if has_issues_export and not issues:
                return jsonify({'success': False, 'error': 'No issues data to export'})
            elif has_links_export and not links:
                return jsonify({'success': False, 'error': 'No links data to export'})
            else:
                return jsonify({'success': False, 'error': 'No data to export'})

        # Return multiple files if we have more than one, otherwise single file
        if len(files_to_export) > 1:
            return jsonify({
                'success': True,
                'multiple_files': True,
                'files': files_to_export
            })
        else:
            # Single file
            file_data = files_to_export[0]
            return jsonify({
                'success': True,
                'content': file_data['content'],
                'mimetype': file_data['mimetype'],
                'filename': file_data['filename']
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/export_all', methods=['POST'])
@login_required
def export_all():
    """Export selected crawl data as a ZIP of JSON files.

    Accepts options: include_urls, include_body_text, include_links,
    include_issues, include_images.  All default to True.
    """
    try:
        data = request.get_json() or {}
        local_data = data.get('localData', {})
        options = data.get('options', {})

        # Export flags (default all True for backwards compat)
        inc_urls       = options.get('urls', True)
        inc_body_text  = options.get('body_text', False)
        inc_links      = options.get('links', True)
        inc_issues     = options.get('issues', True)
        inc_images     = options.get('images', True)
        inc_embeddings = options.get('embeddings', False)
        inc_linkgraph  = options.get('linkgraph', False)

        # Use local data if provided (loaded crawl), otherwise get from crawler
        if local_data and local_data.get('urls'):
            urls = local_data.get('urls', [])
            links = local_data.get('links', [])
            issues = local_data.get('issues', [])
        else:
            crawler = get_or_create_crawler()
            crawl_data = crawler.get_status()
            urls = crawl_data.get('urls', [])
            links = crawl_data.get('links', [])
            issues = crawl_data.get('issues', [])

        if not urls:
            return jsonify({'success': False, 'error': 'No data to export'})

        # Build clean filename prefix for zip contents
        try:
            from urllib.parse import urlparse as _up2
            _d2 = _up2(urls[0].get('url', '')).hostname or 'export'
            _d2 = _d2.replace('www.', '')
        except Exception:
            _d2 = 'export'
        _zp = f'{_d2}_{datetime.now().strftime("%Y-%m-%d")}'

        # Apply issue exclusion patterns
        if issues and inc_issues:
            settings_manager = get_session_settings()
            current_settings = settings_manager.get_settings()
            exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
            exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
            issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

        # Update link statuses
        if links and urls and inc_links:
            status_lookup = {url_data['url']: url_data.get('status_code') for url_data in urls}
            for link in links:
                target_url = link.get('target_url')
                if target_url in status_lookup:
                    link['target_status'] = status_lookup[target_url]

        export_ts = time.strftime('%Y-%m-%d %H:%M:%S')
        ts_file = int(time.time())

        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

            # 1. URLs (SEO data) - optionally strip body_text to keep it light
            if inc_urls:
                if inc_body_text:
                    urls_export = urls
                else:
                    urls_export = [
                        {k: v for k, v in u.items() if k != 'body_text'}
                        for u in urls
                    ]
                urls_json = json.dumps({
                    'export_date': export_ts,
                    'total_urls': len(urls_export),
                    'data': urls_export
                }, indent=2, default=str)
                zf.writestr(f'{_zp}_urls.json', urls_json)

            # 2. Body text (separate lightweight file: url + body_text only)
            if inc_body_text:
                content_data = []
                for u in urls:
                    body = u.get('body_text', '')
                    if body:
                        content_data.append({
                            'url': u.get('url', ''),
                            'title': u.get('title', ''),
                            'h1': u.get('h1', ''),
                            'word_count': u.get('word_count', 0),
                            'body_text': body,
                        })
                if content_data:
                    content_json = json.dumps({
                        'export_date': export_ts,
                        'total_pages': len(content_data),
                        'data': content_data
                    }, indent=2, default=str)
                    zf.writestr(f'{_zp}_content.json', content_json)

            # 3. Links
            if inc_links and links:
                links_json = generate_links_json_export(links)
                zf.writestr(f'{_zp}_links.json', links_json)

                # 3b. Reverse link index (grouped by target URL, internal only)
                reverse_index = {}
                for link in links:
                    if not link.get('is_internal'):
                        continue
                    target = link.get('target_url', '')
                    if target not in reverse_index:
                        reverse_index[target] = []
                    reverse_index[target].append({
                        'source_url': link.get('source_url', ''),
                        'anchor_text': link.get('anchor_text', ''),
                        'placement': link.get('placement', 'body')
                    })
                link_report = sorted(
                    [{'target_url': t, 'count': len(srcs), 'linked_from': srcs}
                     for t, srcs in reverse_index.items()],
                    key=lambda x: x['count'], reverse=True
                )
                zf.writestr(f'{_zp}_link_report.json',
                            json.dumps(link_report, indent=2, default=str))

            # 4. Issues
            if inc_issues and issues:
                issues_json = generate_issues_json_export(issues)
                zf.writestr(f'{_zp}_issues.json', issues_json)

            # 5. Images (flat list, one row per image)
            if inc_images:
                all_images = []
                for url_data in urls:
                    page_url = url_data.get('url', '')
                    for img in url_data.get('images', []):
                        all_images.append({
                            'page_url': page_url,
                            'src': img.get('src', ''),
                            'alt': img.get('alt', ''),
                            'width': img.get('width', ''),
                            'height': img.get('height', ''),
                            'file_size': img.get('file_size', 0),
                            'content_type': img.get('content_type', ''),
                            'loading': img.get('loading', ''),
                        })
                if all_images:
                    images_json = json.dumps({
                        'export_date': export_ts,
                        'total_images': len(all_images),
                        'data': all_images
                    }, indent=2, default=str)
                    zf.writestr(f'{_zp}_images.json', images_json)

            # 6. Embeddings
            if inc_embeddings:
                from src.crawl_db import get_embeddings_for_crawl
                from src.embedder import bytes_to_embedding
                crawl_id = session.get('current_crawl_id')
                if crawl_id:
                    rows = get_embeddings_for_crawl(crawl_id)
                    embed_data = []
                    for row in rows:
                        embed_data.append({
                            'url': row[0],
                            'title': row[1],
                            'embedding_input': row[2],
                            'embedding': bytes_to_embedding(row[3]),
                            'internal_links_out': json.loads(row[4]) if row[4] else [],
                            'token_count': row[5],
                        })
                    if embed_data:
                        embed_json = json.dumps({
                            'export_date': export_ts,
                            'total_pages': len(embed_data),
                            'model': 'text-embedding-3-large',
                            'dimensions': 3072,
                            'data': embed_data
                        }, indent=2)
                        zf.writestr(f'{_zp}_embeddings.json', embed_json)

            # 7. LinkGraph JSON (self-contained internal linking analysis file)
            if inc_linkgraph:
                crawl_id = session.get('current_crawl_id')
                if crawl_id:
                    linkgraph_content = generate_linkgraph_json_export(crawl_id)
                    if linkgraph_content:
                        zf.writestr(f'{_zp}_linkgraph.json', linkgraph_content)

            # 8. Claims
            if options.get('claims', True):
                crawl_id = session.get('current_crawl_id')
                if crawl_id:
                    from src.crawl_db import count_claims
                    if count_claims(crawl_id) > 0:
                        claims_json = generate_claims_json_export(crawl_id, urls)
                        zf.writestr(f'{_zp}_claims.json', claims_json)

                        claims_csv = generate_claims_csv_export(crawl_id, urls)
                        zf.writestr(f'{_zp}_claims.csv', claims_csv)

        buf.seek(0)
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'{_zp}_export_all.zip'
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


def recover_crashed_crawls():
    """Check for and recover any crashed/orphaned crawls on startup.
    - If all URLs were crawled (urls_crawled >= urls_discovered), mark as completed.
    - Otherwise mark as paused (genuinely incomplete, can be resumed).
    """
    try:
        from src.crawl_db import set_crawl_status, get_db

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, base_url, status, urls_crawled, urls_discovered
                FROM crawls
                WHERE status IN ('running', 'paused')
                ORDER BY started_at DESC
            ''')
            orphans = [dict(row) for row in cursor.fetchall()]

        if orphans:
            print("\n" + "=" * 60)
            print("CRASH RECOVERY")
            print("=" * 60)
            for crawl in orphans:
                crawled = crawl.get('urls_crawled') or 0
                discovered = crawl.get('urls_discovered') or 0
                if crawled > 0 and crawled >= discovered:
                    set_crawl_status(crawl['id'], 'completed')
                    print(f"  {crawl['base_url']} (ID: {crawl['id']}): {crawl['status']} → completed ({crawled}/{discovered} URLs)")
                else:
                    # Genuinely incomplete — keep as paused so user can resume
                    if crawl['status'] == 'running':
                        set_crawl_status(crawl['id'], 'paused')
                    print(f"  {crawl['base_url']} (ID: {crawl['id']}): {crawl['status']} → paused ({crawled}/{discovered} URLs, resumable)")
            print("=" * 60 + "\n")
    except Exception as e:
        print(f"Error during crash recovery: {e}")

def graceful_shutdown(signum, frame):
    """Save all active crawls before shutdown"""
    print("\n" + "=" * 60)
    print("GRACEFUL SHUTDOWN")
    print("=" * 60)
    print("Saving all active crawls...")

    try:
        with instances_lock:
            for session_id, instance_data in list(crawler_instances.items()):
                crawler = instance_data['crawler']
                if crawler.is_running and crawler.crawl_id and crawler.db_save_enabled:
                    print(f"  → Saving crawl {crawler.crawl_id}...")
                    try:
                        crawler._save_batch_to_db(force=True)
                        crawler._save_queue_checkpoint()
                        from src.crawl_db import set_crawl_status
                        set_crawl_status(crawler.crawl_id, 'paused')
                    except Exception as e:
                        print(f"    Error saving crawl {crawler.crawl_id}: {e}")

        print("All crawls saved successfully")
        print("=" * 60)
    except Exception as e:
        print(f"Error during shutdown: {e}")

    print("Goodbye!")
    import sys
    sys.exit(0)

def main():
    import signal

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Recover any crashed crawls from previous session
    recover_crashed_crawls()

    # Start cleanup thread for old crawler instances
    start_cleanup_thread()

    print("=" * 60)
    print("LibreCrawl - SEO Spider")
    print("=" * 60)
    print(f"\n🚀 Server starting on http://0.0.0.0:5000")
    print(f"🌐 Access from browser: http://localhost:5000")
    print(f"📱 Access from network: http://<your-ip>:5000")
    print(f"\n✨ Multi-tenancy enabled - each browser session is isolated")
    print(f"💾 Settings stored in browser localStorage")
    print(f"\nPress Ctrl+C to stop the server\n")
    print("=" * 60 + "\n")

    # Open browser in a separate thread after short delay
    def open_browser():
        time.sleep(1.5)  # Wait for Flask to start
        webbrowser.open('http://localhost:5000')

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    # Run Flask server with Waitress (production-grade WSGI server)
    from waitress import serve
    print("Starting LibreCrawl on http://localhost:5000")
    print("Using Waitress WSGI server with multi-threading support")
    serve(app, host='0.0.0.0', port=5000, threads=8)

if __name__ == '__main__':
    main()