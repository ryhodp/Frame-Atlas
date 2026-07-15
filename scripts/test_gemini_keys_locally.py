"""
Frame Atlas — local test for per-user Gemini API keys (Account page Step 4,
Settings spend section). Covers what's testable WITHOUT a real Gemini key:
route access, optional-by-default behavior, key save/read, tag/mine guards,
billing/spend error states, and that admin's shared-key path is untouched.
Actual tagging/interpret calls to the real Gemini API still need a live
manual check.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_gemini_keys_locally.py
"""

import importlib.util
import os
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_gemini_keys_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ["GEMINI_API_KEY"] = "admin-shared-key"
    os.environ["GOOGLE_PICKER_API_KEY"] = "dummy-picker-key"
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.")

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert r.status_code == 200, r.get_json()

    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    r = friend.post("/api/auth/register", json={"invite_code": friend_code, "username": "casey", "password": "friendpass1"})
    assert r.status_code == 200, r.get_json()

    # 1. Admin (user 1) rides the shared env key with no key of their own saved.
    assert mod.get_user_gemini_key(1) == "admin-shared-key"
    print("1. Admin resolves to the shared GEMINI_API_KEY env var.")

    # 2. A friend with no saved key resolves to None (optional by default).
    assert mod.get_user_gemini_key(2) is None
    print("2. A friend with no saved key gets None back (Gemini key truly optional).")

    # 3. /api/account/gemini-key GET reflects "no key yet" before saving.
    r = friend.get("/api/account/gemini-key")
    body = r.get_json()
    assert r.status_code == 200 and body == {"has_key": False, "key_last4": None}, body
    print("3. GET /api/account/gemini-key reports has_key=False before any key is saved.")

    # 4. Friend saves a key; GET reflects it back with only the last 4 chars.
    r = friend.post("/api/account/gemini-key", json={"key": "AIzaSyTESTKEY1234"})
    body = r.get_json()
    assert r.status_code == 200 and body["has_key"] is True and body["key_last4"] == "1234", body
    r = friend.get("/api/account/gemini-key").get_json()
    assert r == {"has_key": True, "key_last4": "1234"}, r
    print("4. Friend can save a Gemini key and read back has_key/last4 (never the full key).")

    # 5. get_user_gemini_key now resolves for that friend.
    assert mod.get_user_gemini_key(2) == "AIzaSyTESTKEY1234"
    print("5. get_user_gemini_key(2) now returns the friend's own saved key.")

    # 6. Posting an empty key is rejected.
    r = friend.post("/api/account/gemini-key", json={"key": "  "})
    assert r.status_code == 400, r.get_json()
    print("6. Posting a blank key is rejected with 400.")

    # 7. /api/billing/spend: admin (shared key) gets real numbers, not an error.
    r = admin.get("/api/billing/spend")
    body = r.get_json()
    assert r.status_code == 200 and body["cost_usd"] == 0.0, body
    print("7. Admin's /api/billing/spend returns zeroed usage (shared key present, no calls made yet).")

    # 8. A second friend with NO saved key gets the clear "add your key" error,
    # not a crash or empty numbers.
    code2 = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend2 = mod.app.test_client()
    friend2.post("/api/auth/register", json={"invite_code": code2, "username": "jordan", "password": "friendpass2"})
    r = friend2.get("/api/billing/spend")
    body = r.get_json()
    assert r.status_code == 400 and body["error"] == "no_key", body
    print("8. Friend with no key gets a clean 'no_key' error from /api/billing/spend, not a crash.")

    # 9. record_gemini_usage tallies tokens into the right user/month and
    # /api/billing/spend reflects it afterward.
    class FakeUsage:
        prompt_token_count = 1000
        candidates_token_count = 500
    mod.record_gemini_usage(2, FakeUsage())
    r = friend.get("/api/billing/spend").get_json()
    expected_cost = round((1000 / 1_000_000) * 0.30 + (500 / 1_000_000) * 2.50, 4)
    assert abs(r["cost_usd"] - expected_cost) < 1e-6, (r, expected_cost)
    assert r["input_tokens"] == 1000 and r["output_tokens"] == 500, r
    print("9. record_gemini_usage() tallies tokens/cost correctly and /api/billing/spend reflects them.")

    # 10. A second usage record in the same month ACCUMULATES rather than overwrites.
    mod.record_gemini_usage(2, FakeUsage())
    r = friend.get("/api/billing/spend").get_json()
    assert r["input_tokens"] == 2000 and r["output_tokens"] == 1000, r
    print("10. A second call in the same month adds to the running total instead of resetting it.")

    # 11. /api/tag/mine refuses admin (admin tags automatically after sync).
    r = admin.post("/api/tag/mine")
    assert r.status_code == 400, r.get_json()
    print("11. Admin can't use /api/tag/mine (their tagging runs automatically after sync).")

    # 12. /api/tag/mine refuses a friend with no key, with a clear message.
    r = friend2.post("/api/tag/mine")
    body = r.get_json()
    assert r.status_code == 400 and "Gemini API key" in body["error"], body
    print("12. /api/tag/mine for a keyless friend returns a clear 'add your key' error, not a crash.")

    # 13. Everything above still requires login.
    anon = mod.app.test_client()
    assert anon.get("/api/account/gemini-key").status_code == 401
    assert anon.get("/api/billing/spend").status_code == 401
    assert anon.post("/api/tag/mine").status_code == 401
    print("13. Logged-out requests still get 401 on every new route.")

    # 14. /api/interpret now requires a per-user key — a keyless friend gets a
    # clean error instead of silently riding the admin's shared key.
    r = friend2.post("/api/interpret", json={"phrase": "moody blue nights"})
    body = r.get_json()
    assert r.status_code == 400 and "Gemini API key" in body["error"], body
    print("14. /api/interpret for a keyless friend returns a clear error (no longer rides the admin's key).")

    print("\nAll per-user Gemini key checks passed.")


if __name__ == "__main__":
    main()
