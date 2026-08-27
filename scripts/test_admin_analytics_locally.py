"""V19: quick check for /api/analytics/users (admin-only cross-account analytics)."""
import importlib.util, io, json, os, sys, tempfile

# Derived from this file's own location, like every other test_*_locally.py.
# This was hardcoded to a path on one developer's Mac, so it could only ever
# run there: on CI the checkout lives at /home/runner/work/..., the open()
# below raised FileNotFoundError, and this script failed every run since the
# Tests workflow was added. It was one of the two reasons CI had never been
# green (the other was a setdefault/assert mismatch in
# test_personal_drive_connect_locally.py).
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))
workdir = tempfile.mkdtemp(prefix="frame_atlas_admin_analytics_")
db_path = os.path.join(workdir, "library.db")

os.environ["FA_DB_PATH"] = db_path

os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
os.environ["GEMINI_API_KEY"] = "dummy"
os.environ["GOOGLE_PICKER_API_KEY"] = "dummy"
os.environ["FLASK_SECRET_KEY"] = "test-not-for-prod"
os.environ["GOOGLE_DRIVE_CREDENTIALS"] = json.dumps({"type": "service_account", "client_email": "robot@test.iam.gserviceaccount.com", "project_id": "test"})

spec = importlib.util.spec_from_file_location("test_app", os.path.join(REPO, "backend", "app.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

admin = mod.app.test_client()
admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
code = admin.post("/api/admin/invite-codes").get_json()["code"]

friend = mod.app.test_client()
r = friend.post("/api/auth/register", json={"invite_code": code, "username": "casey", "email": "casey@test.com", "password": "friendpass1"})
assert r.status_code == 200, r.get_json()

# log casey in for real (register may already set session — logout first to test last_login_at via /login)
friend.post("/api/auth/logout")
r = friend.post("/api/auth/login", json={"username": "casey", "password": "friendpass1"})
assert r.status_code == 200, r.get_json()

# 1. Non-admin gets 403
r = friend.get("/api/analytics/users")
assert r.status_code == 403, r.get_json()
print("1. Non-admin blocked from /api/analytics/users (403). ✅")

# 2. Admin gets 200 with aggregate + users list
r = admin.get("/api/analytics/users")
assert r.status_code == 200, r.get_json()
data = r.get_json()
assert data["aggregate"]["total_users"] == 2, data["aggregate"]
users = {u["username"] if "username" in u else u["name"]: u for u in data["users"]}
print("2. Admin sees aggregate totals + per-user list. ✅", data["aggregate"])

# 3. Casey's row shows a non-null last_login_at (since she just logged in) and correct role/cap
casey = next(u for u in data["users"] if u["name"] == "casey")
assert casey["last_login_at"] is not None, casey
assert casey["role"] == "user"
assert casey["image_cap"] == mod.drive.PERSONAL_LIBRARY_CAP
print("3. Casey's row: last_login_at set, role=user, image_cap enforced. ✅")

# 4. Admin (id 1) has no image cap
ryan = next(u for u in data["users"] if u["id"] == 1)
assert ryan["image_cap"] is None, ryan
print("4. Admin row has no image cap. ✅")

# 5. Seed a couple images for casey and confirm counts + storage reflect it
import sqlite3
conn = sqlite3.connect(db_path)
casey_id = conn.execute("SELECT id FROM users WHERE username='casey'").fetchone()[0]
blob = b"x" * 500
for i in range(3):
    conn.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio) VALUES (?,?,?,?,?,?)",
        (casey_id, f"f{i}", f"f{i}.jpg", blob, "cap", "16:9")
    )
conn.commit(); conn.close()

r = admin.get("/api/analytics/users")
data = r.get_json()
casey = next(u for u in data["users"] if u["id"] == casey_id)
assert casey["image_count"] == 3, casey
assert casey["storage_bytes"] == 1500, casey
assert data["aggregate"]["total_images"] == 3, data["aggregate"]
print("5. Image count + storage_bytes reflect seeded images; aggregate total matches. ✅")

print("\nAll admin analytics checks passed. ✅")
