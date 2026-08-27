"""
Frame Atlas — local test for V46's dead-refresh-token handling in
get_user_credentials().

Real-world trigger: a crop failed with Google's raw error —
    invalid_grant: Token has been expired or revoked.
— which is Google's own doing, not a bug here. The most common cause is the
OAuth consent screen still sitting in "Testing" publishing status in Google
Cloud Console, which caps every refresh token at 7 days no matter how often
the app is used.

Before this fix, get_user_credentials() let creds.refresh() throw straight
out of the function. Every caller was left to fend for itself:
  - the crop worker's broad except caught it and put Google's raw JSON blob
    in the failure toast instead of anything actionable
  - run_db_backup()'s broad except did the same to the Railway log, and kept
    doing it every day until someone noticed
  - /api/drive/picker-token has NO try/except at all — it would have 500'd
    with an unhandled exception on the browser calling Google's Picker
  - /api/account/google-status kept reporting {signed_in: true} forever,
    since it only ever checked the DB column for NULL, never whether the
    token was actually still good — so there was no visible signal telling
    anyone to reconnect

The fix: catch RefreshError specifically, clear the stale token from the DB,
and return None — the exact same value get_user_credentials() already
returns for "never connected". Every call site already null-checks that
case correctly (this is what test_crop_queue_locally.py's "No OAuth client"
checks already pin), so a dead token now degrades to a message every caller
already had, instead of Google's raw error reaching a screen.

This test covers get_user_credentials()/get_user_drive_service() directly,
not the crop worker itself — that's test_crop_queue_locally.py's job, and it
already proves what happens once get_user_drive_service returns None.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_oauth_token_refresh_locally.py
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


def make_token(refresh_token="refresh-tok", expired=True):
    return json.dumps({
        "token": "access-tok",
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "x",
        "client_secret": "y",
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        # google-auth treats a missing/past expiry as expired; a real future
        # date makes the "already valid, don't touch it" case realistic.
        "expiry": "2020-01-01T00:00:00Z" if expired else "2099-01-01T00:00:00Z",
    })


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_oauth_refresh_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_oauth_refresh_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_oauth_refresh_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK.")

    from google.auth.exceptions import RefreshError

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    def make_user(username, token):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, email, password_hash, role, google_oauth_token) "
            "VALUES (?, ?, 'x', 'admin', ?)",
            (username, f"{username}@test.com", token),
        )
        uid = c.lastrowid
        conn.commit()
        conn.close()
        return uid

    def token_in_db(uid):
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT google_oauth_token FROM users WHERE id = ?", (uid,)).fetchone()
        conn.close()
        return row[0]

    # ── 1. dead refresh token: Google's exact error ─────────────────────────
    dead_uid = make_user("dead", make_token(expired=True))

    def refresh_invalid_grant(self, request):
        raise RefreshError(
            "invalid_grant: Token has been expired or revoked.",
            {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
        )
    mod.drive.UserCredentials.refresh = refresh_invalid_grant

    result = mod.drive.get_user_credentials(dead_uid)
    check("Dead refresh token: get_user_credentials returns None (not a raised exception)", result is None, result)
    check("Dead refresh token: DB column cleared to NULL", token_in_db(dead_uid) is None, token_in_db(dead_uid))

    service = mod.drive.get_user_drive_service(dead_uid)
    check("Dead refresh token: get_user_drive_service also degrades to None", service is None, service)

    # ── 2. never connected: unchanged baseline behaviour ─────────────────────
    never_uid = make_user("never", None)
    result = mod.drive.get_user_credentials(never_uid)
    check("Never connected: get_user_credentials returns None (baseline unchanged)", result is None, result)

    # ── 3. already-valid token: must NOT call refresh() or touch the DB ──────
    valid_uid = make_user("valid", make_token(expired=False))

    def refresh_should_not_be_called(self, request):
        raise AssertionError("refresh() must not be called on a non-expired token")
    mod.drive.UserCredentials.refresh = refresh_should_not_be_called

    before = token_in_db(valid_uid)
    result = mod.drive.get_user_credentials(valid_uid)
    check("Already-valid token: returned without calling refresh()", result is not None, result)
    check("Already-valid token: DB left untouched", token_in_db(valid_uid) == before)

    # ── 4. expired but genuinely refreshable: happy path still works ─────────
    refreshable_uid = make_user("refreshable", make_token(expired=True))

    def refresh_success(self, request):
        self.token = "new-access-token"
    mod.drive.UserCredentials.refresh = refresh_success

    result = mod.drive.get_user_credentials(refreshable_uid)
    check("Refreshable token: get_user_credentials returns real creds, not None", result is not None, result)
    new_token_row = token_in_db(refreshable_uid)
    check(
        "Refreshable token: DB updated with the refreshed token (happy path unbroken)",
        new_token_row is not None and json.loads(new_token_row).get("token") == "new-access-token",
        new_token_row,
    )

    # ── 5. a DIFFERENT exception type must NOT be swallowed ──────────────────
    # Only RefreshError should trigger the "disconnect" behaviour. A network
    # blip (or anything else) must keep failing loudly/retriably rather than
    # silently unlinking a perfectly good Google connection.
    other_uid = make_user("other-error", make_token(expired=True))

    def refresh_network_error(self, request):
        raise ConnectionError("temporary network failure")
    mod.drive.UserCredentials.refresh = refresh_network_error

    raised = False
    try:
        mod.drive.get_user_credentials(other_uid)
    except ConnectionError:
        raised = True
    check("Non-RefreshError exceptions still propagate (not silently swallowed)", raised)
    check(
        "Non-RefreshError exceptions do NOT clear the token",
        token_in_db(other_uid) is not None,
        token_in_db(other_uid),
    )

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED: {failures}")
        sys.exit(1)
    print("ALL OAUTH TOKEN REFRESH TESTS PASSED ✅")


if __name__ == "__main__":
    main()
