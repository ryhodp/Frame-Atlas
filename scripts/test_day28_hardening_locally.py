"""
Frame Atlas — Day 28 (Phase 3) local tests.

Covers the two things Day 28 shipped:

  1. The structural split — core.py + schema.py exist, app.py imports every
     moved name back by its bare name (so scripts/test_*_locally.py that reach
     into `mod.` keep working), init_db() takes run_self_test as a parameter,
     and mod.DB_PATH is still readable.

  2. Security hardening —
       - session cookies carry HttpOnly always, and Secure whenever the app is
         NOT running locally;
       - /api/auth/register and /api/auth/forgot-password are IP-rate-limited
         (hand-rolled, rate_limit_hits table), disabled while local, and fail
         open on a DB error.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_day28_hardening_locally.py
"""

import importlib.util
import os
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


def load_app(db_path):
    os.environ["FA_DB_PATH"] = db_path
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    name = "day28_app_" + os.path.basename(os.path.dirname(db_path))
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_day28_")
    db_path = os.path.join(workdir, "library.db")
    mod = load_app(db_path)
    print("App imported OK.\n")

    # ---- 1. the structural split ----------------------------------------
    print("--- core.py / schema.py split ---")
    import core
    import schema
    check("core.get_db and app.get_db are the same object", mod.get_db is core.get_db)
    check("core.db_path resolves the active FA_DB_PATH", core.db_path() == db_path)
    check("app.DB_PATH is still readable and correct", mod.DB_PATH == db_path)
    check("schema.init_db is what app imported", mod.init_db is schema.init_db)
    check("run_self_test still lives in app.py, not schema", hasattr(mod, "run_self_test") and not hasattr(schema, "run_self_test"))
    check("init_db accepts run_self_test as a kwarg",
          "run_self_test" in mod.init_db.__code__.co_varnames)
    check("moved tag helpers came back by bare name",
          mod.normalize_tag_value("Cars") == "car" and mod.CAT_LABELS["my_work"] == "My Work")
    check("the rate_limit_hits table was created", _table_exists(db_path, "rate_limit_hits"))

    # a fresh boot still self-tests against the real DB (the callback wiring)
    conn = mod.get_db()
    results = mod.run_self_test(conn)
    conn.close()
    check("run_self_test runs and passes after the split", results and all(ok for _, ok, _ in results))

    # ---- 2a. cookie flags --------------------------------------------------
    print("\n--- session cookie flags ---")
    check("SESSION_COOKIE_HTTPONLY is True", mod.app.config["SESSION_COOKIE_HTTPONLY"] is True)
    check("SESSION_COOKIE_SAMESITE unchanged (Lax)", mod.app.config["SESSION_COOKIE_SAMESITE"] == "Lax")
    check("SESSION_COOKIE_SECURE is False while local (FA_DB_PATH set)",
          mod.app.config["SESSION_COOKIE_SECURE"] is False)
    check("RUNNING_LOCALLY is True in this harness", mod.RUNNING_LOCALLY is True)

    # ---- 2b. rate limiting ----------------------------------------------
    print("\n--- rate limiting on public auth endpoints ---")
    client = mod.app.test_client()
    # local => disabled: hammering register never yields a 429
    codes = []
    for i in range(mod.RATE_LIMIT_MAX + 4):
        r = client.post("/api/auth/register", json={
            "invite_code": "bogus", "username": f"u{i}", "email": f"u{i}@x.com", "password": "password1"})
        codes.append(r.status_code)
    check("rate limiting is OFF locally (no 429s)", 429 not in codes)

    # flip to production behaviour and exercise the real table
    mod.RUNNING_LOCALLY = False
    with mod.app.test_request_context("/", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}):
        seq = [mod._rate_limited("probe") for _ in range(mod.RATE_LIMIT_MAX + 2)]
        check("first RATE_LIMIT_MAX hits pass", seq[:mod.RATE_LIMIT_MAX] == [False] * mod.RATE_LIMIT_MAX)
        check("the next hits are blocked", seq[mod.RATE_LIMIT_MAX] is True and seq[-1] is True)
        check("a different scope is counted separately", mod._rate_limited("other") is False)
        check("_client_ip trusts the LAST X-Forwarded-For entry", mod._client_ip() == "10.0.0.1")
    with mod.app.test_request_context("/", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2"}):
        check("a different client IP has its own budget", mod._rate_limited("probe") is False)

    # endpoint returns 429 once over the limit
    prod_client = mod.app.test_client()
    prod_client.post("/api/setup", json={"email": "admin@x.com", "password": "adminpass123"})
    with_headers = {"X-Forwarded-For": "198.51.100.42"}
    reg_codes = [prod_client.post("/api/auth/register",
                                  json={"invite_code": "bogus", "username": f"n{i}",
                                        "email": f"n{i}@x.com", "password": "password1"},
                                  headers=with_headers).status_code
                 for i in range(mod.RATE_LIMIT_MAX + 3)]
    check("register 400s early, 429s once abused", reg_codes[0] == 400 and reg_codes[-1] == 429)
    fp_codes = [prod_client.post("/api/auth/forgot-password", json={"email": "no@x.com"},
                                 headers=with_headers).status_code
                for i in range(mod.RATE_LIMIT_MAX + 3)]
    check("forgot-password 429s once abused", 429 in fp_codes)

    # fails open if the table is unusable
    mod._orig_get_db = mod.get_db
    def boom():
        raise RuntimeError("db exploded")
    mod.get_db = boom
    try:
        with mod.app.test_request_context("/", headers={"X-Forwarded-For": "192.0.2.1"}):
            check("_rate_limited fails OPEN on a DB error", mod._rate_limited("probe") is False)
    finally:
        mod.get_db = mod._orig_get_db

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def _table_exists(db_path, name):
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    conn.close()
    return row is not None


if __name__ == "__main__":
    main()
