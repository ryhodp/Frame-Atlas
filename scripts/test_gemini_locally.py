"""
Frame Atlas — local test for Day 30 (V72): the Gemini key/usage layer split
into backend/gemini.py.

These functions were previously exercised only through test_gemini_keys_locally.py
and test_security_hardening_locally.py. This gives the module its own direct
coverage:
  - the split wiring: app.py exposes `gemini`, the moved names are NOT bare
    globals on app.py anymore
  - encrypt_secret / decrypt_secret round-trip WITH a key configured
  - the enc:v1: prefix marks encrypted values; plaintext passes through
  - graceful fallback with NO key configured (never crashes, plaintext in/out)
  - a wrong key decrypts to None, never to raw ciphertext
  - set/get_user_gemini_key through a real DB, admin rides the env key
  - record_gemini_usage accumulates within a calendar month

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_gemini_locally.py
"""

import importlib.util
import os
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

PASS = 0
FAIL = 0


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}" + (f"  ({extra!r})" if extra is not None else ""))


def main():
    from cryptography.fernet import Fernet

    workdir = tempfile.mkdtemp(prefix="frame_atlas_gemini_test_")
    os.environ["FA_DB_PATH"] = os.path.join(workdir, "library.db")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ["GEMINI_API_KEY"] = "admin-shared-key"
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    ENC_KEY = Fernet.generate_key().decode()
    os.environ["FA_ENCRYPTION_KEY"] = ENC_KEY

    spec = importlib.util.spec_from_file_location("fa_gemini_test_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_gemini_test_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK.")

    gemini = mod.gemini

    # ── 1. the split wiring ─────────────────────────────────────────────────
    print("\n1. Split wiring")
    moved = [
        "_fernet", "encrypt_secret", "decrypt_secret",
        "set_user_gemini_key", "get_user_gemini_key", "record_gemini_usage",
        "ENCRYPTED_PREFIX",
    ]
    for name in moved:
        check(f"gemini.{name} exists", hasattr(gemini, name))
    leaked = [n for n in moved if n in vars(mod)]
    check("no moved name leaked back onto app.py's namespace", leaked == [], leaked)
    check("gemini.py imports only get_db/get_model_pricing/GEMINI_MODEL from core",
          gemini.get_db is mod.get_db)

    # ── 2. encrypt/decrypt round-trip WITH a key ────────────────────────────
    print("\n2. encrypt/decrypt with FA_ENCRYPTION_KEY set")
    SECRET = "AIzaSyTESTKEY-abcdefghijklmnop-1234"
    enc = gemini.encrypt_secret(SECRET)
    check("ciphertext carries the enc:v1: prefix", enc.startswith(gemini.ENCRYPTED_PREFIX), enc[:20])
    check("ciphertext is not the plaintext", enc != SECRET)
    check("decrypt_secret round-trips", gemini.decrypt_secret(enc) == SECRET, gemini.decrypt_secret(enc))
    check("a value with no prefix is treated as legacy plaintext and passes through",
          gemini.decrypt_secret("legacy-plain-key") == "legacy-plain-key")
    check("empty/None encrypt_secret is a no-op", gemini.encrypt_secret("") == "" and gemini.encrypt_secret(None) is None)

    # ── 3. wrong key fails safe ─────────────────────────────────────────────
    print("\n3. wrong key -> None, never ciphertext")
    os.environ["FA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    check("value encrypted with a different key decrypts to None",
          gemini.decrypt_secret(enc) is None, gemini.decrypt_secret(enc))
    check("a tampered value decrypts to None",
          gemini.decrypt_secret(gemini.ENCRYPTED_PREFIX + "gAAAAABtampered") is None)
    os.environ["FA_ENCRYPTION_KEY"] = ENC_KEY

    # ── 4. no key configured -> graceful plaintext fallback ─────────────────
    print("\n4. no FA_ENCRYPTION_KEY -> plaintext fallback (never crashes)")
    os.environ.pop("FA_ENCRYPTION_KEY", None)
    check("_fernet() returns None with no key set", gemini._fernet() is None)
    check("encrypt_secret falls back to plaintext", gemini.encrypt_secret("some-key") == "some-key")
    check("decrypt_secret reads that plaintext back fine", gemini.decrypt_secret("some-key") == "some-key")
    os.environ["FA_ENCRYPTION_KEY"] = ENC_KEY

    # ── 5. set/get_user_gemini_key through a real DB ────────────────────────
    print("\n5. set/get_user_gemini_key + admin env key")
    admin = mod.app.test_client()
    assert admin.post("/api/setup", json={"email": "a@a.com", "password": "testpass123"}).status_code == 200
    check("admin (user 1) rides the shared GEMINI_API_KEY env var",
          gemini.get_user_gemini_key(1) == "admin-shared-key", gemini.get_user_gemini_key(1))
    code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    assert friend.post("/api/auth/register", json={
        "invite_code": code, "username": "casey", "email": "c@c.com", "password": "friendpass1"
    }).status_code == 200
    check("friend with no saved key gets None", gemini.get_user_gemini_key(2) is None)
    gemini.set_user_gemini_key(2, SECRET)
    check("friend's saved key round-trips back out decrypted",
          gemini.get_user_gemini_key(2) == SECRET, gemini.get_user_gemini_key(2))

    import sqlite3
    raw = sqlite3.connect(os.environ["FA_DB_PATH"]).execute(
        "SELECT gemini_api_key FROM users WHERE id = 2").fetchone()[0]
    check("the stored column value is encrypted at rest, not the plaintext",
          raw != SECRET and raw.startswith(gemini.ENCRYPTED_PREFIX), raw[:20])

    # the qualified call site in app.py still works end to end
    r = friend.get("/api/account/gemini-key").get_json()
    check("GET /api/account/gemini-key reports has_key True, last4 only, never the full key",
          r.get("has_key") is True and r.get("key_last4") == SECRET[-4:] and SECRET not in str(r), r)

    # ── 6. record_gemini_usage accumulates within a month ───────────────────
    print("\n6. record_gemini_usage")

    class FakeUsage:
        prompt_token_count = 1000
        candidates_token_count = 500

    gemini.record_gemini_usage(2, FakeUsage())
    gemini.record_gemini_usage(2, FakeUsage())
    gemini.record_gemini_usage(2, None)  # no metadata -> silently ignored
    conn = sqlite3.connect(os.environ["FA_DB_PATH"])
    row = conn.execute(
        "SELECT input_tokens, output_tokens FROM gemini_usage WHERE user_id = 2").fetchone()
    check("two calls accumulate (not overwrite) within the same month",
          row == (2000, 1000), row)

    print(f"\n{PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)
    print("All gemini.py checks passed.")


if __name__ == "__main__":
    main()
