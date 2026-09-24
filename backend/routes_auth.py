"""routes_auth.py — Frame Atlas auth routes as a Flask Blueprint (Day 36 / Phase 3).

Login, logout, register, one-time admin setup, forgot/reset password, and
invite-code management. Every route here is character-for-character what it
was in app.py — only @app.route became @bp.route, and every URL path is
byte-identical, so the frontend needed zero changes.

The login-lockout and rate-limit helpers below (_login_lock_remaining,
_rate_limited, etc.) are used ONLY by the routes in this file, so they moved
here rather than to core.py — unlike admin_required/PUBLIC_API_ROUTES/
RUNNING_LOCALLY, which core.py needs because app.py's login gate and (in
time) other blueprints all read them too.

import core (bare, not `from core import RUNNING_LOCALLY`) is deliberate for
_rate_limited()'s local-mode check: a `from` import binds a private copy in
this module's own namespace at import time, which test_day28_hardening_locally.py
could never override afterward — reading `core.RUNNING_LOCALLY` qualified
means a test's `core.RUNNING_LOCALLY = False` (using the SAME core module
object, cached once in sys.modules) actually reaches this function.
"""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash, check_password_hash

import core
from core import get_db, admin_required

bp = Blueprint('auth', __name__)


def current_user_row():
    uid = session.get('user_id')
    if not uid:
        return None
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT id, username, email, role FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    return row


# ── V44 (Day 26): LOGIN THROTTLING ──────────────────────────────────────────
# Before this there was no limit at all: passwords could be guessed as fast as
# requests could be sent. werkzeug's pbkdf2 hashing was already correct, but
# hashing only makes a STOLEN database expensive to crack — it does nothing
# about guessing against a live login form.
#
# Keyed on the ACCOUNT, never the caller's IP address. Two reasons, both
# deliberate: an attacker can rotate IPs freely (so IP throttling buys almost
# nothing), and this app is explicitly used from shared networks — CLAUDE.md's
# own V43 notes cite hotel and set wifi as normal conditions — where one
# guesser would otherwise lock out everyone else sharing that connection.
LOGIN_LOCK_THRESHOLD = 5      # wrong passwords allowed before any lock kicks in
LOGIN_LOCK_BASE_SECONDS = 30  # first lock; doubles per failure past the threshold
LOGIN_LOCK_MAX_SECONDS = 3600  # ceiling, so an account is never bricked outright

def _login_lock_remaining(locked_until):
    """Seconds still to wait on a lock, or 0 if not locked. An unparseable
    timestamp counts as NOT locked — a corrupted value must never be able to
    permanently lock a real user out of their own account."""
    if not locked_until:
        return 0
    try:
        until = datetime.fromisoformat(str(locked_until))
    except (TypeError, ValueError) as e:
        print(f"[auth] Ignoring unparseable login_locked_until value {locked_until!r}: {e}")
        return 0
    return max(0, int((until - datetime.now()).total_seconds()))

def _format_lock_wait(seconds):
    """Human wording for the retry message — '45 seconds' / '3 minutes'."""
    if seconds < 60:
        return f'{seconds} second{"" if seconds == 1 else "s"}'
    minutes = (seconds + 59) // 60
    return f'{minutes} minute{"" if minutes == 1 else "s"}'

def _record_failed_login(row):
    """Bump the consecutive-failure counter and, past the threshold, set an
    exponentially growing lockout window."""
    count = (row['failed_login_count'] or 0) + 1
    locked_until = None
    if count >= LOGIN_LOCK_THRESHOLD:
        # 5th failure -> base, 6th -> 2x, 7th -> 4x, ... capped.
        wait = min(LOGIN_LOCK_BASE_SECONDS * (2 ** (count - LOGIN_LOCK_THRESHOLD)), LOGIN_LOCK_MAX_SECONDS)
        locked_until = datetime.now() + timedelta(seconds=wait)
        print(f"[auth] '{row['username']}' hit {count} consecutive failed logins — locked for {wait}s")

    conn = get_db()
    conn.execute(
        'UPDATE users SET failed_login_count = ?, login_locked_until = ? WHERE id = ?',
        (count, locked_until.isoformat(sep=' ', timespec='seconds') if locked_until else None, row['id'])
    )
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Day 28 (Phase 3): rate limiting on the public, unauthenticated auth
# endpoints. Login already has per-account throttling (V44); this caps
# /api/auth/register and /api/auth/forgot-password, which have no account to
# key on yet. Hand-rolled against a tiny table (rate_limit_hits, built in
# schema.py) — same reasoning as the V44 lockout: no Flask-Limiter dependency,
# no added Railway deploy time.
#
# Keyed on IP here (unlike the login lockout, which is per-account) — it's the
# only identifier a pre-account request has. Best-effort by nature: an
# attacker behind many IPs isn't stopped, but casual scripted abuse from one
# host is. Disabled entirely for local runs so the test suite and local dev
# aren't throttled.
RATE_LIMIT_MAX = 5              # allowed hits per IP...
RATE_LIMIT_WINDOW_SECONDS = 60  # ...per this rolling window

def _client_ip():
    """Best-guess caller IP. ProxyFix only normalises proto/host, not
    X-Forwarded-For, so read the header ourselves. Take the LAST entry — the
    one Railway's own proxy appended — since earlier entries are
    client-supplied and trivially spoofed."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[-1].strip() or 'unknown'
    return request.remote_addr or 'unknown'

def _rate_limited(scope):
    """Record this request and return True if this IP has already exceeded
    RATE_LIMIT_MAX hits on `scope` within the window. Fails OPEN on any DB
    error (returns False) — a limiter that 500s the endpoint it guards is
    worse than the abuse it prevents. No-op locally."""
    if core.RUNNING_LOCALLY:
        return False
    ip = _client_ip()
    now = datetime.now()
    cutoff = (now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)).isoformat(sep=' ', timespec='seconds')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('DELETE FROM rate_limit_hits WHERE hit_at < ?', (cutoff,))
        recent = c.execute(
            'SELECT COUNT(*) FROM rate_limit_hits WHERE scope = ? AND client_ip = ? AND hit_at >= ?',
            (scope, ip, cutoff)
        ).fetchone()[0]
        c.execute('INSERT INTO rate_limit_hits (scope, client_ip, hit_at) VALUES (?, ?, ?)',
                  (scope, ip, now.isoformat(sep=' ', timespec='seconds')))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ratelimit] check failed for {scope} ({e}) — allowing request through")
        return False
    if recent >= RATE_LIMIT_MAX:
        print(f"[ratelimit] {ip} exceeded {RATE_LIMIT_MAX}/{RATE_LIMIT_WINDOW_SECONDS}s on {scope}")
        return True
    return False


@bp.route('/api/setup/status')
def setup_status():
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT password_hash FROM users WHERE id = 1').fetchone()
    conn.close()
    return jsonify({'needs_setup': not bool(row and row['password_hash'])})

@bp.route('/api/setup', methods=['POST'])
def setup_admin():
    """One-time admin bootstrap. The moment user 1 has a password set, this
    route refuses forever — the password itself is the lock, so there's no
    separate flag to leave open by mistake."""
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT password_hash FROM users WHERE id = 1').fetchone()
    if row and row['password_hash']:
        conn.close()
        return jsonify({'error': 'Setup already completed'}), 403

    data = request.get_json(force=True) or {}
    password = data.get('password') or ''
    email = (data.get('email') or '').strip()
    if len(password) < 8:
        conn.close()
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if not email:
        conn.close()
        return jsonify({'error': 'Email is required'}), 400

    c.execute(
        'UPDATE users SET password_hash = ?, email = ?, role = ? WHERE id = 1',
        (generate_password_hash(password), email, 'admin')
    )
    conn.commit()
    conn.close()

    session['user_id'] = 1
    session['username'] = 'ryan'
    session['role'] = 'admin'
    return jsonify({'success': True, 'user': {'id': 1, 'username': 'ryan', 'email': email, 'role': 'admin'}})

@bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT id, username, email, role, password_hash, failed_login_count, login_locked_until '
        'FROM users WHERE username = ? COLLATE NOCASE',
        (username,)
    ).fetchone()
    conn.close()

    # V44 (Day 26): the lockout check runs BEFORE the password check, so a
    # locked account can't be probed at all — otherwise the throttle would
    # still leak "was that the right password?" one attempt at a time.
    if row:
        locked_for = _login_lock_remaining(row['login_locked_until'])
        if locked_for > 0:
            print(f"[auth] Rejected login for '{row['username']}' — locked for another {locked_for}s")
            return jsonify({
                'error': f'Too many failed attempts. Try again in {_format_lock_wait(locked_for)}.',
                'locked': True,
                'retry_after_seconds': locked_for,
            }), 429

    if not row or not row['password_hash'] or not check_password_hash(row['password_hash'], password):
        if row:
            _record_failed_login(row)
        return jsonify({'error': 'Invalid username or password'}), 401

    conn = get_db()
    # A successful login clears the throttle — the counter tracks CONSECUTIVE
    # failures, so a legitimate user who mistypes twice then gets it right
    # starts clean rather than creeping toward a lockout over weeks.
    conn.execute(
        "UPDATE users SET last_login_at = CURRENT_TIMESTAMP, failed_login_count = 0, "
        "login_locked_until = NULL WHERE id = ?",
        (row['id'],)
    )
    conn.commit()
    conn.close()

    session['user_id'] = row['id']
    session['username'] = row['username']
    session['role'] = row['role']
    return jsonify({'success': True, 'user': {
        'id': row['id'], 'username': row['username'], 'email': row['email'], 'role': row['role']
    }})

@bp.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@bp.route('/api/auth/me')
def me():
    if not session.get('user_id'):
        return jsonify({'logged_in': False})
    row = current_user_row()
    if not row:
        session.clear()
        return jsonify({'logged_in': False})
    return jsonify({'logged_in': True, 'user': {
        'id': row['id'], 'username': row['username'], 'email': row['email'], 'role': row['role']
    }})

@bp.route('/api/auth/register', methods=['POST'])
def register():
    if _rate_limited('register'):
        return jsonify({'error': 'Too many attempts. Please wait a minute and try again.'}), 429
    data = request.get_json(force=True) or {}
    invite_code = (data.get('invite_code') or '').strip()
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not invite_code or not username or not email or len(password) < 8:
        return jsonify({'error': 'Invite code, username, email, and an 8+ character password are all required'}), 400
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'That email address doesn\'t look right'}), 400

    conn = get_db()
    c = conn.cursor()

    invite = c.execute(
        'SELECT id FROM invite_codes WHERE code = ? AND used_by IS NULL', (invite_code,)
    ).fetchone()
    if not invite:
        conn.close()
        return jsonify({'error': 'Invite code is invalid or already used'}), 400

    if c.execute('SELECT 1 FROM users WHERE username = ? COLLATE NOCASE', (username,)).fetchone():
        conn.close()
        return jsonify({'error': 'That username is taken'}), 400
    if c.execute('SELECT 1 FROM users WHERE email = ? COLLATE NOCASE', (email,)).fetchone():
        conn.close()
        return jsonify({'error': 'An account with that email already exists'}), 400

    c.execute(
        'INSERT INTO users (username, password_hash, role, email) VALUES (?, ?, ?, ?)',
        (username, generate_password_hash(password), 'user', email)
    )
    new_user_id = c.lastrowid
    c.execute(
        'UPDATE invite_codes SET used_by = ?, used_at = CURRENT_TIMESTAMP WHERE id = ?',
        (new_user_id, invite['id'])
    )
    conn.commit()
    conn.close()

    session['user_id'] = new_user_id
    session['username'] = username
    session['role'] = 'user'
    return jsonify({'success': True, 'user': {'id': new_user_id, 'username': username, 'email': email, 'role': 'user'}})


@bp.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    # NOTE (Day 28): this endpoint still returns the reset token in its JSON
    # response — no email is sent yet. Until email delivery is wired up
    # (Flask-Mail / Mailgun), treat this as an ADMIN-ONLY recovery path, not
    # something to point friends at. The token is 256-bit, one-time-use, and
    # expires in 1 hour, but a token in an HTTP response is a token anyone on
    # the wire or in a log can use. See CLAUDE.md → Auth.
    if _rate_limited('forgot-password'):
        return jsonify({'error': 'Too many attempts. Please wait a minute and try again.'}), 429
    data = request.get_json(force=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    conn = get_db()
    c = conn.cursor()
    user = c.execute('SELECT id, username FROM users WHERE email = ? COLLATE NOCASE', (email,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'No account uses that email address'}), 404

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    c.execute(
        'INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)',
        (user['id'], token, expires_at)
    )
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'username': user['username'], 'reset_path': f'/reset-password?token={token}'})


@bp.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json(force=True) or {}
    token = (data.get('token') or '').strip()
    password = data.get('password') or ''
    if not token or len(password) < 8:
        return jsonify({'error': 'A valid reset link and an 8+ character password are required'}), 400

    conn = get_db()
    c = conn.cursor()
    reset = c.execute(
        'SELECT id, user_id, expires_at, used_at FROM password_resets WHERE token = ?', (token,)
    ).fetchone()
    if not reset or reset['used_at'] or datetime.utcnow() > datetime.fromisoformat(reset['expires_at']):
        conn.close()
        return jsonify({'error': 'This reset link is invalid or has expired. Request a new one.'}), 400

    c.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(password), reset['user_id']))
    c.execute('UPDATE password_resets SET used_at = CURRENT_TIMESTAMP WHERE id = ?', (reset['id'],))
    conn.commit()
    conn.close()

    return jsonify({'success': True})

@bp.route('/api/admin/invite-codes', methods=['GET'])
@admin_required
def list_invite_codes():
    conn = get_db()
    c = conn.cursor()
    rows = c.execute('''
        SELECT ic.id, ic.code, ic.created_at, ic.used_at, u.username AS used_by_username
        FROM invite_codes ic
        LEFT JOIN users u ON u.id = ic.used_by
        ORDER BY ic.created_at DESC
    ''').fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'], 'code': r['code'], 'created_at': r['created_at'],
        'used_at': r['used_at'], 'used_by_username': r['used_by_username']
    } for r in rows])

@bp.route('/api/admin/invite-codes', methods=['POST'])
@admin_required
def create_invite_code():
    code = secrets.token_urlsafe(8)
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO invite_codes (code, created_by) VALUES (?, ?)', (code, session['user_id']))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': new_id, 'code': code})

@bp.route('/api/admin/invite-codes/<int:invite_id>', methods=['DELETE'])
@admin_required
def revoke_invite_code(invite_id):
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT used_by FROM invite_codes WHERE id = ?', (invite_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Invite code not found'}), 404
    if row['used_by'] is not None:
        conn.close()
        return jsonify({'error': 'Already used, cannot revoke'}), 400
    c.execute('DELETE FROM invite_codes WHERE id = ?', (invite_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})
