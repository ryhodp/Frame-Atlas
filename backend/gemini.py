"""Frame Atlas — Gemini keys & usage (V72 / Day 30).

Phase 3 refactor: the friend-API-key encryption and per-user spend tracking
lifted out of app.py, character-for-character. Depends only on core.py and
the `cryptography` package (imported lazily inside _fernet). app.py does
`import gemini` and every call site is qualified (`gemini.get_user_gemini_key`,
`gemini.record_gemini_usage`, …).

This module is the key/crypto/billing layer only — the actual Gemini API
client calls (tagging worker, NL interpret) still live in app.py and move to
tagging.py on Day 32.
"""

import os
from datetime import datetime

from core import get_db, get_model_pricing, GEMINI_MODEL

# ============================================================================
# GEMINI KEYS & USAGE
# ============================================================================

# V44 (Day 26): friends' Gemini keys used to sit in users.gemini_api_key as
# plain readable text. They're real credentials that bill to a friend's own
# Google account, so a leaked copy of library.db (which travels: the monthly
# Drive backup, any local copy) meant usable keys. Now encrypted at rest with
# Fernet (AES-128-CBC + HMAC authentication, from `cryptography`).
#
# The encryption key lives in its own Railway env var, NOT derived from
# FLASK_SECRET_KEY — one secret protecting two unrelated things means
# rotating it for a session-security reason would silently destroy every
# stored API key, and vice versa.
#
# Values are stored with an "enc:v1:" prefix so encrypted and legacy
# plaintext rows are always distinguishable. There is no migration pass: a
# plaintext key is read as-is and silently re-encrypted the next time it's
# saved (see set_user_gemini_key), because we can't decrypt what was never
# encrypted and forcing friends to re-paste their keys would break their
# tagging with no warning.
ENCRYPTED_PREFIX = 'enc:v1:'

def _fernet():
    """The app's Fernet cipher, or None if FA_ENCRYPTION_KEY isn't set.

    Returning None rather than raising is deliberate: a missing key must not
    take the whole app down at import time (it'd break every route, not just
    Gemini features). Callers fall back to storing plaintext exactly as
    before V44, and log loudly — so an unset env var degrades to the old
    behaviour instead of silently losing keys."""
    raw = os.environ.get('FA_ENCRYPTION_KEY', '').strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(raw.encode())
    except Exception as e:
        print(f"[crypto] FA_ENCRYPTION_KEY is set but unusable ({e}) — "
              "falling back to plaintext storage. Generate a valid key with: "
              "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        return None

def encrypt_secret(plaintext):
    """Encrypt a secret for storage. Returns plaintext unchanged (and warns)
    if no encryption key is configured, so saving a key never hard-fails."""
    if not plaintext:
        return plaintext
    f = _fernet()
    if f is None:
        print("[crypto] WARNING: storing a secret in PLAINTEXT — FA_ENCRYPTION_KEY is not set on this deploy.")
        return plaintext
    return ENCRYPTED_PREFIX + f.encrypt(plaintext.encode()).decode()

def decrypt_secret(stored):
    """Read a stored secret. Anything without the enc: prefix is a legacy
    plaintext row and comes back as-is — that's what keeps keys saved before
    V44 working without a migration."""
    if not stored or not stored.startswith(ENCRYPTED_PREFIX):
        return stored
    f = _fernet()
    if f is None:
        print("[crypto] ERROR: found an encrypted secret but FA_ENCRYPTION_KEY is not set — cannot decrypt.")
        return None
    try:
        return f.decrypt(stored[len(ENCRYPTED_PREFIX):].encode()).decode()
    except Exception as e:
        # Wrong key, or a corrupted/tampered value — Fernet authenticates, so
        # this catches both. Never fall back to returning the ciphertext: it
        # would be sent to Google as an API key and fail confusingly.
        #
        # Log the exception TYPE, not just str(e): Fernet's InvalidToken
        # carries an empty message, so "({e})" alone printed literally
        # "()" — a log line that says nothing is the exact problem the
        # V44 except:pass audit exists to fix.
        reason = str(e) or type(e).__name__
        print(f"[crypto] ERROR: could not decrypt stored secret ({reason}) — "
              "wrong FA_ENCRYPTION_KEY, or the value was corrupted. Treating as missing.")
        return None

def set_user_gemini_key(user_id, key):
    """Save a user's Gemini key, encrypted. The single write path, so a key
    can never be stored unencrypted by some other route later."""
    conn = get_db()
    conn.execute('UPDATE users SET gemini_api_key = ? WHERE id = ?', (encrypt_secret(key), user_id))
    conn.commit()
    conn.close()

def get_user_gemini_key(user_id):
    """Admin (user 1) rides the shared Railway env key. Everyone else must
    have saved their own key in Account settings — a friend's AI tagging and
    NL search run on their own key/budget, never the admin's.

    V44: stored keys are encrypted at rest; decrypt_secret() transparently
    passes through rows saved as plaintext before that change."""
    if user_id == 1:
        return os.environ.get('GEMINI_API_KEY')
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if not row or not row['gemini_api_key']:
        return None
    return decrypt_secret(row['gemini_api_key'])

def record_gemini_usage(user_id, usage_metadata, model_name=None):
    """Adds one API response's token counts to this user's running total for
    the current calendar month, so Settings can show an estimated spend."""
    if not usage_metadata:
        return
    pricing = get_model_pricing(model_name or GEMINI_MODEL)
    input_tokens = getattr(usage_metadata, 'prompt_token_count', 0) or 0
    output_tokens = getattr(usage_metadata, 'candidates_token_count', None)
    if output_tokens is None:
        output_tokens = getattr(usage_metadata, 'response_token_count', 0) or 0
    cost = (input_tokens / 1_000_000) * pricing['input'] + (output_tokens / 1_000_000) * pricing['output']

    month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO gemini_usage (user_id, month, input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, month) DO UPDATE SET
            input_tokens = input_tokens + excluded.input_tokens,
            output_tokens = output_tokens + excluded.output_tokens,
            cost_usd = cost_usd + excluded.cost_usd
    ''', (user_id, month, input_tokens, output_tokens, cost))
    conn.commit()
    conn.close()
