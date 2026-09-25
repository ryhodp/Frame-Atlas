"""routes_account.py — a user's account + Google connection, as a Flask Blueprint (Day 42 / Phase 3).

The new-user setup checklist (/api/account/setup-status), a friend's own
Gemini key (/api/account/gemini-key, encrypted at rest via gemini.py) and
their monthly spend (/api/billing/spend), plus connecting / disconnecting the
Google account used for uploads (/api/auth/status, /api/auth/google/login,
/callback, /disconnect, /api/drive/picker-token).

NOT Frame Atlas login — that's routes_auth.py. The OAuth redirect URI is built
from a fixed path string (request.url_root + '/api/auth/google/callback'),
never url_for(), so moving these routes into a blueprint cannot change it.

Every route body is character-for-character what it was in app.py.
"""
from datetime import datetime

from flask import Blueprint, jsonify, redirect, request, session

from core import get_db, current_user_id
import drive
import gemini

bp = Blueprint('account', __name__)


@bp.route('/api/account/setup-status', methods=['GET'])
def account_setup_status():
    """V17: everything the Home-page setup checklist and Account page need in
    one call — robot email to share with, whether a folder is connected,
    library size, and whether a Gemini key is saved."""
    user_id = session['user_id']
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id, folder_name, last_sync FROM sync_settings WHERE user_id = ?', (user_id,))
    folder = c.fetchone()
    image_count = c.execute('SELECT COUNT(*) FROM images WHERE user_id = ?', (user_id,)).fetchone()[0]
    key_row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return jsonify({
        'service_account_email': drive.get_service_account_email(),
        'folder_connected': bool(folder and folder['folder_id']),
        'folder_name': folder['folder_name'] if folder else None,
        'last_sync': folder['last_sync'] if folder else None,
        'image_count': image_count,
        'image_cap': None if user_id == 1 else drive.PERSONAL_LIBRARY_CAP,
        'has_gemini_key': user_id == 1 or bool(key_row and key_row['gemini_api_key']),
    })

@bp.route('/api/account/gemini-key', methods=['GET', 'POST'])
def account_gemini_key():
    """Non-admin users save their own Gemini key here (admin rides the shared
    Railway env var — see get_user_gemini_key). Fully optional: skipping this
    just means a friend's synced photos stay untagged but searchable."""
    uid = current_user_id()
    conn = get_db()
    c = conn.cursor()

    if request.method == 'POST':
        key = (request.get_json() or {}).get('key', '').strip()
        if not key:
            conn.close()
            return jsonify({'error': 'No key provided'}), 400
        conn.close()
        # V44: goes through set_user_gemini_key so it's encrypted at rest.
        # key_last4 is computed from what the user just typed, never read
        # back out of the database.
        gemini.set_user_gemini_key(uid, key)
        return jsonify({'success': True, 'has_key': True, 'key_last4': key[-4:]})

    row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    key = gemini.decrypt_secret(row['gemini_api_key']) if row and row['gemini_api_key'] else None
    return jsonify({'has_key': bool(key), 'key_last4': key[-4:] if key else None})

@bp.route('/api/billing/spend')
def billing_spend():
    """This month's estimated Gemini spend for the logged-in user. Only
    meaningful for someone with a usable key (admin's shared key, or a
    friend's own saved key) — everyone else gets a clear next step instead."""
    uid = current_user_id()
    if not gemini.get_user_gemini_key(uid):
        return jsonify({
            'error': 'no_key',
            'message': 'Add your Gemini API key in Account settings to track your spend.'
        }), 400

    month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT input_tokens, output_tokens, cost_usd FROM gemini_usage WHERE user_id = ? AND month = ?',
        (uid, month)
    ).fetchone()
    conn.close()

    return jsonify({
        'month': month,
        'input_tokens': row['input_tokens'] if row else 0,
        'output_tokens': row['output_tokens'] if row else 0,
        'cost_usd': round(row['cost_usd'], 4) if row else 0.0,
    })

@bp.route('/api/auth/status')
def auth_status():
    """Day 8: admin-only (upload sign-in). Day 14 Stage 2: generalized to
    whoever's logged in, since every user now connects their own Google
    account — session['user_id'] instead of a hardcoded 1."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT google_oauth_token FROM users WHERE id = ?', (session['user_id'],))
    row = c.fetchone()
    conn.close()
    return jsonify({'signed_in': bool(row and row['google_oauth_token'])})

@bp.route('/api/auth/google/login')
def google_login():
    redirect_uri = request.url_root.rstrip('/') + '/api/auth/google/callback'
    flow = drive.get_oauth_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type='offline', prompt='consent', include_granted_scopes='true')
    session['oauth_state'] = state
    return redirect(auth_url)

@bp.route('/api/drive/picker-token')
def drive_picker_token():
    """A short-lived OAuth access token for the CURRENT user, handed to the
    Google Picker widget in the browser so they can pick a folder from their
    own Drive. Same drive.file scope as everything else here — the Picker is
    what lets that narrow scope reach an arbitrary folder the user chooses,
    without ever requesting broader Drive access."""
    creds = drive.get_user_credentials(session['user_id'])
    if not creds:
        return jsonify({'error': 'not_signed_in'}), 401
    return jsonify({'access_token': creds.token})

@bp.route('/api/auth/google/callback')
def google_callback():
    redirect_uri = request.url_root.rstrip('/') + '/api/auth/google/callback'
    flow = drive.get_oauth_flow(redirect_uri)
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as e:
        return redirect(f'/?auth_error={e}')

    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET google_oauth_token = ? WHERE id = ?', (flow.credentials.to_json(), session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/?signed_in=1')

@bp.route('/api/auth/google/disconnect', methods=['POST'])
def google_disconnect():
    """Clear the user's Google OAuth token. Re-authenticates on next upload attempt."""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET google_oauth_token = NULL WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()
    return jsonify({'success': True})
