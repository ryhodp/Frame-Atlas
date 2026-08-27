"""
Frame Atlas — local test for Day 26 (V44): security + reliability hardening.

Covers:
  - Login throttling: consecutive failures counted, lockout after the
    threshold, escalating wait, a locked account rejected BEFORE the password
    check (so a lock can't be probed), success clearing the counter, and a
    corrupted lock timestamp never bricking an account.
  - Gemini key encryption at rest: keys stored encrypted (not readable in the
    raw DB file), round-tripping correctly, legacy plaintext rows still
    readable with no migration, a wrong/missing encryption key failing safe
    rather than handing ciphertext to Google as an API key.
  - The except:pass audit: unexpected migration errors now log instead of
    vanishing, while the routine "column already exists" case stays quiet.

Same trick as the other test_*_locally.py scripts: boots the server against a throwaway database and drives it through Flask's test client.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python3 scripts/test_security_hardening_locally.py
"""

import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))

passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  — {detail}" if detail else ""))


def boot_app(workdir, encryption_key=None):
    """Import app.py against its own DB."""
    db_path = os.path.join(workdir, "library.db")
    os.environ["FA_DB_PATH"] = db_path
    app_path = os.path.join(REPO, "backend", "app.py")

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    if encryption_key is None:
        os.environ.pop("FA_ENCRYPTION_KEY", None)
    else:
        os.environ["FA_ENCRYPTION_KEY"] = encryption_key

    modname = f"test_app_{os.path.basename(workdir)}"
    spec = importlib.util.spec_from_file_location(modname, app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, db_path


def main():
    from cryptography.fernet import Fernet
    enc_key = Fernet.generate_key().decode()

    workdir = tempfile.mkdtemp(prefix="frame_atlas_security_test_")
    mod, db_path = boot_app(workdir, encryption_key=enc_key)
    print("App imported OK.\n")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ── 0. Migration added the throttling columns ───────────────────────────
    print("--- migration ---")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    check("users.failed_login_count column exists", "failed_login_count" in cols)
    check("users.login_locked_until column exists", "login_locked_until" in cols)

    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'admin@test.com', 'password': 'correctpass123'})
    check("admin setup succeeds", setup_r.status_code == 200, setup_r.get_json())

    # ── 1. Wrong passwords are counted ──────────────────────────────────────
    print("\n--- login throttling ---")
    fresh = mod.app.test_client()
    r = fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    check("a wrong password is rejected with 401", r.status_code == 401, r.get_json())
    count = c.execute("SELECT failed_login_count FROM users WHERE id = 1").fetchone()[0]
    check("failed_login_count incremented to 1", count == 1, f"got {count}")

    # ── 2. Below the threshold, no lock ─────────────────────────────────────
    for _ in range(mod.LOGIN_LOCK_THRESHOLD - 2):
        fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    row = c.execute("SELECT failed_login_count, login_locked_until FROM users WHERE id = 1").fetchone()
    check(f"after {mod.LOGIN_LOCK_THRESHOLD - 1} failures still not locked",
          row["login_locked_until"] is None, f"locked_until={row['login_locked_until']}")

    # ── 3. Crossing the threshold locks the account with a 429 ──────────────
    r = fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    row = c.execute("SELECT failed_login_count, login_locked_until FROM users WHERE id = 1").fetchone()
    check(f"{mod.LOGIN_LOCK_THRESHOLD}th failure sets a lockout",
          row["login_locked_until"] is not None)
    r = fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    check("a locked account returns 429, not 401", r.status_code == 429, r.get_json())
    body = r.get_json()
    check("429 response tells the client how long to wait",
          body.get('locked') is True and isinstance(body.get('retry_after_seconds'), int),
          body)

    # ── 4. THE key property: a locked account rejects the CORRECT password ──
    # If the lock only applied to wrong passwords it would still leak
    # "that was the right one" and the throttle would be worthless.
    r = fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'correctpass123'})
    check("a locked account rejects even the CORRECT password (lock can't be probed)",
          r.status_code == 429, r.get_json())

    # ── 5. Escalation: each further failure lengthens the wait ──────────────
    c.execute("UPDATE users SET login_locked_until = NULL WHERE id = 1")
    conn.commit()
    fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    first_until = c.execute("SELECT login_locked_until FROM users WHERE id = 1").fetchone()[0]
    c.execute("UPDATE users SET login_locked_until = NULL WHERE id = 1")
    conn.commit()
    fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    second_until = c.execute("SELECT login_locked_until FROM users WHERE id = 1").fetchone()[0]
    check("each additional failure produces a longer lockout",
          datetime.fromisoformat(second_until) > datetime.fromisoformat(first_until),
          f"{first_until} -> {second_until}")

    # ── 6. Lock capped, never permanent ─────────────────────────────────────
    c.execute("UPDATE users SET failed_login_count = 99, login_locked_until = NULL WHERE id = 1")
    conn.commit()
    fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'wrong'})
    until = c.execute("SELECT login_locked_until FROM users WHERE id = 1").fetchone()[0]
    wait = (datetime.fromisoformat(until) - datetime.now()).total_seconds()
    check("lockout is capped (an account is never bricked outright)",
          wait <= mod.LOGIN_LOCK_MAX_SECONDS + 5, f"{wait}s")

    # ── 7. A corrupted lock timestamp must not brick the account ────────────
    c.execute("UPDATE users SET login_locked_until = 'not-a-timestamp' WHERE id = 1")
    conn.commit()
    check("an unparseable lock timestamp reads as NOT locked",
          mod._login_lock_remaining('not-a-timestamp') == 0)

    # ── 8. A successful login clears the throttle ───────────────────────────
    c.execute("UPDATE users SET login_locked_until = NULL, failed_login_count = 3 WHERE id = 1")
    conn.commit()
    r = fresh.post('/api/auth/login', json={'username': 'ryan', 'password': 'correctpass123'})
    check("correct password succeeds once unlocked", r.status_code == 200, r.get_json())
    row = c.execute("SELECT failed_login_count, login_locked_until FROM users WHERE id = 1").fetchone()
    check("a successful login resets the failure counter to 0",
          row["failed_login_count"] == 0 and row["login_locked_until"] is None, dict(row))

    # ── 9. An unknown username doesn't 500 or create rows ───────────────────
    r = fresh.post('/api/auth/login', json={'username': 'nobody-here', 'password': 'whatever'})
    check("unknown username still returns a plain 401", r.status_code == 401, r.get_json())

    # ── 10. Gemini key encryption ───────────────────────────────────────────
    print("\n--- gemini key encryption ---")
    SECRET = "AIzaSyTESTKEY-abcdefghijklmnop-1234"
    c.execute("INSERT INTO invite_codes (code, created_by) VALUES ('SECCODE1', 1)")
    conn.commit()
    friend = mod.app.test_client()
    reg = friend.post('/api/auth/register', json={
        'email': 'friend@test.com', 'password': 'friendpass123',
        'username': 'friend', 'invite_code': 'SECCODE1'
    })
    check("friend registers", reg.status_code == 200, reg.get_json())
    friend_uid = c.execute("SELECT id FROM users WHERE username = 'friend'").fetchone()[0]

    r = friend.post('/api/account/gemini-key', json={'key': SECRET})
    check("friend saves a Gemini key", r.status_code == 200, r.get_json())
    check("save response masks the key to last 4 only",
          r.get_json().get('key_last4') == SECRET[-4:] and SECRET not in str(r.get_json()),
          r.get_json())

    stored = c.execute("SELECT gemini_api_key FROM users WHERE id = ?", (friend_uid,)).fetchone()[0]
    check("stored value is NOT the plaintext key", stored != SECRET)
    check("stored value carries the enc:v1: marker", stored.startswith(mod.gemini.ENCRYPTED_PREFIX), stored[:20])

    # The whole point: the raw DB file must not contain the key as readable text.
    conn.commit()
    with open(db_path, "rb") as fh:
        raw_db = fh.read()
    check("the secret does not appear in plaintext anywhere in the DB file",
          SECRET.encode() not in raw_db)

    check("get_user_gemini_key decrypts back to the original",
          mod.gemini.get_user_gemini_key(friend_uid) == SECRET,
          mod.gemini.get_user_gemini_key(friend_uid))

    r = friend.get('/api/account/gemini-key')
    check("GET reports has_key with the correct last4",
          r.get_json().get('has_key') is True and r.get_json().get('key_last4') == SECRET[-4:],
          r.get_json())
    check("GET never returns the full key", SECRET not in str(r.get_json()))

    # ── 11. Legacy plaintext rows still work (no migration needed) ──────────
    LEGACY = "AIzaSyLEGACY-plaintext-key-5678"
    c.execute("UPDATE users SET gemini_api_key = ? WHERE id = ?", (LEGACY, friend_uid))
    conn.commit()
    check("a pre-V44 plaintext key is still readable (no migration required)",
          mod.gemini.get_user_gemini_key(friend_uid) == LEGACY,
          mod.gemini.get_user_gemini_key(friend_uid))

    # Re-saving it should encrypt it going forward.
    friend.post('/api/account/gemini-key', json={'key': LEGACY})
    stored = c.execute("SELECT gemini_api_key FROM users WHERE id = ?", (friend_uid,)).fetchone()[0]
    check("re-saving a legacy key upgrades it to encrypted storage",
          stored.startswith(mod.gemini.ENCRYPTED_PREFIX))

    # ── 12. Wrong encryption key fails safe ─────────────────────────────────
    # Must return None, never the raw ciphertext — handing ciphertext to
    # Google as an API key would fail in a confusing, hard-to-diagnose way.
    other_key = Fernet.generate_key().decode()
    saved_env = os.environ.get("FA_ENCRYPTION_KEY")
    os.environ["FA_ENCRYPTION_KEY"] = other_key
    check("a value encrypted with a different key decrypts to None, not ciphertext",
          mod.gemini.decrypt_secret(stored) is None, mod.gemini.decrypt_secret(stored))
    os.environ["FA_ENCRYPTION_KEY"] = saved_env

    # ── 13. Tampered ciphertext is rejected (Fernet authenticates) ──────────
    tampered = mod.gemini.ENCRYPTED_PREFIX + "gAAAAABmZmZmZmZmZmZmZmZmZmZmtampered"
    check("a tampered/corrupt encrypted value decrypts to None",
          mod.gemini.decrypt_secret(tampered) is None)

    # ── 14. No encryption key configured = graceful plaintext fallback ──────
    os.environ.pop("FA_ENCRYPTION_KEY", None)
    check("with no FA_ENCRYPTION_KEY, encrypt_secret falls back to plaintext (never crashes)",
          mod.gemini.encrypt_secret("some-key") == "some-key")
    check("with no FA_ENCRYPTION_KEY, plaintext still reads back fine",
          mod.gemini.decrypt_secret("some-key") == "some-key")
    os.environ["FA_ENCRYPTION_KEY"] = saved_env

    # ── 15. The except:pass audit helper ────────────────────────────────────
    print("\n--- except:pass audit ---")
    check("_is_duplicate_column_error recognizes the routine case",
          mod._is_duplicate_column_error(Exception("duplicate column name: foo")))
    check("_is_duplicate_column_error does NOT swallow a real error",
          not mod._is_duplicate_column_error(Exception("database or disk is full")))

    conn.close()

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
