import sqlite3
import hashlib
import os
from flask import Flask, request, jsonify, session, send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
DB = os.path.join(os.environ.get("DATA_DIR", "."), "todo.db")


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                assigned_to INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'open',
                deadline TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (assigned_to) REFERENCES users(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );
        """)
        # Počiatoční používatelia (username, heslo, zobrazované meno, je_admin)
        users = [
            ("Karcsi", "PZZsQcTueGPz", "Karol Czodor", 1),
            ("Zsani",  "AY3zpjS3Xynb", "Zsani Csomor", 0),
            ("Ani",    "CxVYvqS9NXCT", "Anikó Szekács", 0),
            ("Pityu",  "idLTxGm9vVbj", "Szakolci Štefan", 0),
            ("Tibor",  "svz2G47TXPZy", "Kiss Tibor", 0),
        ]
        for username, pw, name, is_admin in users:
            hashed = hashlib.sha256(pw.encode()).hexdigest()
            try:
                conn.execute(
                    "INSERT INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, ?)",
                    (username, hashed, name, is_admin)
                )
            except sqlite3.IntegrityError:
                pass


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def require_login(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Nie ste prihlásený"}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (data["username"], hash_pw(data["password"]))
        ).fetchone()
    if not user:
        return jsonify({"error": "Nesprávne meno alebo heslo"}), 401
    session["user_id"] = user["id"]
    session["display_name"] = user["display_name"]
    session["is_admin"] = user["is_admin"]
    return jsonify({"id": user["id"], "display_name": user["display_name"],
                    "username": user["username"], "is_admin": user["is_admin"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "user_id" not in session:
        return jsonify(None)
    return jsonify({"id": session["user_id"], "display_name": session["display_name"],
                    "is_admin": session.get("is_admin", 0)})


@app.route("/api/users")
@require_login
def users():
    with get_db() as conn:
        rows = conn.execute("SELECT id, display_name, username, is_admin FROM users ORDER BY display_name").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks", methods=["GET"])
@require_login
def get_tasks():
    filter_type = request.args.get("filter", "all")
    uid = session["user_id"]
    with get_db() as conn:
        if filter_type == "mine":
            rows = conn.execute("""
                SELECT t.*, u1.display_name as assigned_name, u2.display_name as creator_name
                FROM tasks t
                JOIN users u1 ON t.assigned_to = u1.id
                JOIN users u2 ON t.created_by = u2.id
                WHERE t.assigned_to = ?
                ORDER BY t.created_at DESC
            """, (uid,)).fetchall()
        elif filter_type == "created":
            rows = conn.execute("""
                SELECT t.*, u1.display_name as assigned_name, u2.display_name as creator_name
                FROM tasks t
                JOIN users u1 ON t.assigned_to = u1.id
                JOIN users u2 ON t.created_by = u2.id
                WHERE t.created_by = ?
                ORDER BY t.created_at DESC
            """, (uid,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.*, u1.display_name as assigned_name, u2.display_name as creator_name
                FROM tasks t
                JOIN users u1 ON t.assigned_to = u1.id
                JOIN users u2 ON t.created_by = u2.id
                ORDER BY t.created_at DESC
            """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks", methods=["POST"])
@require_login
def create_task():
    data = request.json
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, description, assigned_to, created_by, priority, deadline, created_at) VALUES (?,?,?,?,?,?,?)",
            (data["title"], data.get("description", ""), data["assigned_to"],
             session["user_id"], data.get("priority", "medium"), data.get("deadline"), now)
        )
        task_id = cur.lastrowid
        row = conn.execute("""
            SELECT t.*, u1.display_name as assigned_name, u2.display_name as creator_name
            FROM tasks t JOIN users u1 ON t.assigned_to=u1.id JOIN users u2 ON t.created_by=u2.id
            WHERE t.id=?
        """, (task_id,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
@require_login
def update_task(task_id):
    data = request.json
    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Úloha nenájdená"}), 404
        uid = session["user_id"]
        if task["assigned_to"] != uid and task["created_by"] != uid:
            return jsonify({"error": "Nemáte oprávnenie"}), 403
        allowed = {"status", "title", "description", "priority", "deadline", "assigned_to"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"error": "Nič na aktualizáciu"}), 400
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", (*updates.values(), task_id))
        row = conn.execute("""
            SELECT t.*, u1.display_name as assigned_name, u2.display_name as creator_name
            FROM tasks t JOIN users u1 ON t.assigned_to=u1.id JOIN users u2 ON t.created_by=u2.id
            WHERE t.id=?
        """, (task_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@require_login
def delete_task(task_id):
    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Úloha nenájdená"}), 404
        if task["created_by"] != session["user_id"]:
            return jsonify({"error": "Môže mazať iba zadávateľ"}), 403
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return jsonify({"ok": True})


def require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Nie ste prihlásený"}), 401
        if not session.get("is_admin"):
            return jsonify({"error": "Len administrátor môže vykonať túto akciu"}), 403
        return f(*args, **kwargs)
    return wrapper


@app.route("/api/users", methods=["POST"])
@require_admin
def add_user():
    data = request.json
    is_admin = 1 if data.get("is_admin") else 0
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password, display_name, is_admin) VALUES (?,?,?,?)",
                (data["username"], hash_pw(data["password"]), data["display_name"], is_admin)
            )
        return jsonify({"ok": True}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Používateľ už existuje"}), 400


@app.route("/api/users/<int:user_id>/password", methods=["PATCH"])
@require_admin
def change_password(user_id):
    data = request.json
    new_pw = (data.get("password") or "").strip()
    if len(new_pw) < 6:
        return jsonify({"error": "Heslo musí mať aspoň 6 znakov"}), 400
    with get_db() as conn:
        target = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            return jsonify({"error": "Používateľ nenájdený"}), 404
        conn.execute("UPDATE users SET password=? WHERE id=?", (hash_pw(new_pw), user_id))
    return jsonify({"ok": True})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@require_admin
def delete_user(user_id):
    if user_id == session["user_id"]:
        return jsonify({"error": "Nemôžete vymazať sám seba"}), 400
    with get_db() as conn:
        target = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            return jsonify({"error": "Používateľ nenájdený"}), 404
        assigned = conn.execute("SELECT COUNT(*) c FROM tasks WHERE assigned_to=? OR created_by=?",
                                (user_id, user_id)).fetchone()["c"]
        if assigned > 0:
            return jsonify({"error": "Používateľ má priradené úlohy, najprv ich presuňte alebo vymažte"}), 400
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    print("Todo app beží na http://localhost:5000")
    app.run(debug=False, host="0.0.0.0", port=5000)
