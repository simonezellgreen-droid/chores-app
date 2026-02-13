from __future__ import annotations

import os
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Dict, List, Tuple, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, request, redirect, url_for, render_template_string, session, abort

# ============================================================
# SETTINGS (set these in Render -> Environment)
# ============================================================

APP_NAME = "Chores Tracker"

DATABASE_URL = os.environ.get("DATABASE_URL", "")  # Neon/Supabase/Render Postgres URL
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
FAMILY_CODE = os.environ.get("FAMILY_CODE", "")    # one shared code for your family
PARENT_PIN = os.environ.get("PARENT_PIN", "")      # parent-only pages

if not DATABASE_URL:
    # Don't crash immediately—show a helpful page instead.
    DATABASE_OK = False
else:
    DATABASE_OK = True

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ============================================================
# DATABASE HELPERS
# ============================================================

def db():
    # Note: psycopg2 connects using DATABASE_URL
    return psycopg2.connect(DATABASE_URL)

def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def init_db():
    if not DATABASE_OK:
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS families (
                id SERIAL PRIMARY KEY,
                code_hash TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS kids (
                id SERIAL PRIMARY KEY,
                family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                goal_cents INTEGER NOT NULL DEFAULT 0,
                UNIQUE(family_id, name)
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS chores (
                id SERIAL PRIMARY KEY,
                family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
                chore_key TEXT NOT NULL,
                title TEXT NOT NULL,
                reward_cents INTEGER NOT NULL,
                requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE(family_id, chore_key)
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                id SERIAL PRIMARY KEY,
                family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
                kid_name TEXT NOT NULL,
                chore_key TEXT NOT NULL,
                chore_title TEXT NOT NULL,
                reward_cents INTEGER NOT NULL,
                ts DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL
            );
            """)

def get_or_create_family_id() -> int:
    code_hash = sha16(FAMILY_CODE)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM families WHERE code_hash=%s", (code_hash,))
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute("INSERT INTO families(code_hash) VALUES(%s) RETURNING id", (code_hash,))
            return int(cur.fetchone()[0])

def seed_default_chores(family_id: int):
    defaults = [
        ("make_bed", "Make bed", 50, False),
        ("dishes", "Unload dishwasher", 100, True),
        ("trash", "Take out trash", 75, False),
        ("homework", "Homework (checked)", 100, True),
    ]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chores WHERE family_id=%s", (family_id,))
            count = int(cur.fetchone()[0])
            if count > 0:
                return
            for key, title, cents, req in defaults:
                cur.execute(
                    "INSERT INTO chores(family_id, chore_key, title, reward_cents, requires_approval) VALUES(%s,%s,%s,%s,%s)",
                    (family_id, key, title, cents, req),
                )

def dollars(cents: int) -> str:
    return f"${cents/100:.2f}"


# ============================================================
# AUTH (Family code + Parent pin)
# ============================================================

@app.before_request
def require_family_login():
    # Allow access to login page and static
    if request.endpoint in ("family_login", "static", "db_help"):
        return

    # If DB is not set, show a setup page for any route
    if not DATABASE_OK:
        return redirect(url_for("db_help"))

    # Require family login
    if not session.get("family_ok"):
        return redirect(url_for("family_login"))

def require_parent():
    if not session.get("parent_ok"):
        return redirect(url_for("parent_login"))

@app.get("/db-help")
def db_help():
    # Shown if DATABASE_URL is missing
    page = """
    <h1>Database not set</h1>
    <p>This app needs a DATABASE_URL (Postgres) to remember chores on the free plan.</p>
    <p>In Render: Service → Environment → add <b>DATABASE_URL</b>, then redeploy.</p>
    """
    return render_template_string(BASE, title="Setup", body=page)

@app.get("/login")
def family_login():
    # Note: no hints
    page = """
    <h1>Family Login</h1>
    <div class="card">
      <form method="post" action="{{ url_for('family_login_post') }}">
        <div class="row">
          <input name="code" placeholder="Family Code" required />
          <button class="btn btn-primary" type="submit">Enter</button>
        </div>
      </form>
    </div>
    """
    return render_template_string(BASE, title="Login", body=page)

@app.post("/login")
def family_login_post():
    entered = (request.form.get("code") or "").strip()

    # No hints:
    if not entered or entered != FAMILY_CODE:
        # just reload login silently
        session.clear()
        return redirect(url_for("family_login"))

    session.clear()
    session["family_ok"] = True
    # parent_ok stays False until parent logs in
    return redirect(url_for("home"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("family_login"))

@app.get("/parent/login")
def parent_login():
    page = """
    <h1>Parent Login</h1>
    <div class="card">
      <form method="post" action="{{ url_for('parent_login_post') }}">
        <div class="row">
          <input name="pin" placeholder="Parent PIN" required />
          <button class="btn btn-primary" type="submit">Enter</button>
        </div>
      </form>
    </div>
    """
    return render_template_string(BASE, title="Parent Login", body=page)

@app.post("/parent/login")
def parent_login_post():
    pin = (request.form.get("pin") or "").strip()
    if not pin or pin != PARENT_PIN:
        session["parent_ok"] = False
        return redirect(url_for("parent_login"))
    session["parent_ok"] = True
    return redirect(url_for("parent_dashboard"))


# ============================================================
# COMPUTATIONS (owed, streaks, totals)
# ============================================================

def today_local() -> date:
    return datetime.now().date()

def ledger_rows(family_id: int, kid_name: Optional[str] = None) -> List[dict]:
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if kid_name:
                cur.execute(
                    "SELECT * FROM ledger WHERE family_id=%s AND kid_name=%s ORDER BY id DESC",
                    (family_id, kid_name),
                )
            else:
                cur.execute("SELECT * FROM ledger WHERE family_id=%s ORDER BY id DESC", (family_id,))
            return list(cur.fetchall())

def chores_rows(family_id: int) -> List[dict]:
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM chores WHERE family_id=%s ORDER BY title ASC", (family_id,))
            return list(cur.fetchall())

def kids_rows(family_id: int) -> List[dict]:
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM kids WHERE family_id=%s ORDER BY name ASC", (family_id,))
            return list(cur.fetchall())

def owed_by_kid(family_id: int) -> Dict[str, int]:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kid_name, COALESCE(SUM(reward_cents),0)
                FROM ledger
                WHERE family_id=%s AND status='approved'
                GROUP BY kid_name
            """, (family_id,))
            out = {}
            for kid, total in cur.fetchall():
                out[str(kid)] = int(total)
            return out

def totals_by_kid(family_id: int) -> Dict[str, int]:
    # total chores logged (pending + approved + paid)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kid_name, COUNT(*)
                FROM ledger
                WHERE family_id=%s AND status IN ('pending','approved','paid')
                GROUP BY kid_name
            """, (family_id,))
            return {str(k): int(c) for k, c in cur.fetchall()}

def paid_total_by_kid(family_id: int) -> Dict[str, int]:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kid_name, COALESCE(SUM(reward_cents),0)
                FROM ledger
                WHERE family_id=%s AND status='paid'
                GROUP BY kid_name
            """, (family_id,))
            return {str(k): int(s) for k, s in cur.fetchall()}

def streak_for_kid(family_id: int, kid_name: str) -> int:
    # streak = consecutive days (ending today) where kid logged at least one chore
    rows = ledger_rows(family_id, kid_name)
    days = set()
    for r in rows:
        ts = float(r["ts"])
        d = datetime.fromtimestamp(ts).date()
        if r["status"] in ("pending", "approved", "paid"):
            days.add(d)

    streak = 0
    d = today_local()
    while d in days:
        streak += 1
        d = d - timedelta(days=1)
    return streak


# ============================================================
# CORE ACTIONS
# ============================================================

def ensure_family_ready() -> int:
    init_db()
    family_id = get_or_create_family_id()
    seed_default_chores(family_id)
    return family_id

def add_kid_db(family_id: int, name: str):
    name = name.strip()
    if not name:
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kids(family_id,name) VALUES(%s,%s) ON CONFLICT (family_id,name) DO NOTHING",
                (family_id, name),
            )

def set_goal_db(family_id: int, kid_name: str, goal_cents: int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kids SET goal_cents=%s WHERE family_id=%s AND name=%s",
                (goal_cents, family_id, kid_name),
            )

def add_or_update_chore_db(family_id: int, key: str, title: str, cents: int, requires_approval: bool):
    key = key.strip()
    title = title.strip()
    if not key or not title:
        return
    if cents < 0:
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO chores(family_id, chore_key, title, reward_cents, requires_approval)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT (family_id, chore_key)
                DO UPDATE SET title=EXCLUDED.title, reward_cents=EXCLUDED.reward_cents, requires_approval=EXCLUDED.requires_approval
            """, (family_id, key, title, cents, requires_approval))

def delete_chore_db(family_id: int, key: str):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chores WHERE family_id=%s AND chore_key=%s", (family_id, key))

def submit_chore_db(family_id: int, kid_name: str, chore_key: str):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM chores WHERE family_id=%s AND chore_key=%s",
                (family_id, chore_key),
            )
            chore = cur.fetchone()
            if not chore:
                return
            status = "pending" if bool(chore["requires_approval"]) else "approved"
            cur.execute("""
                INSERT INTO ledger(family_id, kid_name, chore_key, chore_title, reward_cents, ts, status)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
            """, (
                family_id,
                kid_name,
                chore_key,
                chore["title"],
                int(chore["reward_cents"]),
                time.time(),
                status
            ))

def approve_deny_db(family_id: int, ledger_id: int, approve: bool):
    new_status = "approved" if approve else "denied"
    with db() as conn:
        with conn.cursor() as cur:
            # only pending can change
            cur.execute("""
                UPDATE ledger
                SET status=%s
                WHERE family_id=%s AND id=%s AND status='pending'
            """, (new_status, family_id, ledger_id))

def mark_paid_db(family_id: int, kid_name: str):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ledger
                SET status='paid'
                WHERE family_id=%s AND kid_name=%s AND status='approved'
            """, (family_id, kid_name))


# ============================================================
# UI TEMPLATES
# ============================================================

BASE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title }}</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 18px; max-width: 980px; }
    a { color: #111; }
    .top a { margin-right: 12px; }
    .card { border: 1px solid #e6e6e6; border-radius: 14px; padding: 14px; margin: 14px 0; }
    .row { display:flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .btn { display:inline-block; padding: 10px 12px; border-radius: 12px; border: 1px solid #ccc; background: #fafafa; text-decoration: none; color: #111; cursor:pointer; }
    .btn-primary { background:#111; color:#fff; border-color:#111; }
    .btn-danger { background:#fff5f5; border-color:#ffcccc; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #eee; text-align:left; vertical-align: top; }
    input, select { padding: 9px; border-radius: 12px; border: 1px solid #ccc; }
    small { color:#666; }
  </style>
</head>
<body>
  <div class="top">
    <a href="{{ url_for('home') }}">Home</a>
    <a href="{{ url_for('kid_menu') }}">Kid</a>
    <a href="{{ url_for('parent_dashboard') }}">Parent</a>
    <a href="{{ url_for('logout') }}">Logout</a>
  </div>
  <hr />
  {{ body|safe }}
</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def home():
    family_id = ensure_family_ready()
    page = """
    <h1>{{ app_name }}</h1>

    <div class="card">
      <h2>Main Menu</h2>
      <div class="row">
        <a class="btn btn-primary" href="{{ url_for('kid_menu') }}">Kid Menu</a>
        <a class="btn btn-primary" href="{{ url_for('parent_dashboard') }}">Parent Dashboard</a>
      </div>
      <p><small>Tip: Parent pages need the Parent PIN.</small></p>
    </div>
    """
    return render_template_string(BASE, title="Home", body=render_template_string(page, app_name=APP_NAME))

@app.get("/kid")
def kid_menu():
    family_id = ensure_family_ready()
    kids = kids_rows(family_id)
    chores = chores_rows(family_id)

    page = """
    <h1>Kid Menu</h1>

    <div class="card">
      <h2>Log a chore</h2>
      {% if kids|length == 0 %}
        <p>No kids yet. Ask a parent to add you in Parent Dashboard.</p>
      {% else %}
        <form method="post" action="{{ url_for('kid_log') }}">
          <div class="row">
            <select name="kid_name" required>
              {% for k in kids %}
                <option value="{{ k['name'] }}">{{ k['name'] }}</option>
              {% endfor %}
            </select>

            <select name="chore_key" required>
              {% for c in chores %}
                <option value="{{ c['chore_key'] }}">
                  {{ c['title'] }} ({{ dollars(c['reward_cents']) }})
                  {% if c['requires_approval'] %} [needs approval]{% else %} [auto]{% endif %}
                </option>
              {% endfor %}
            </select>

            <button class="btn btn-primary" type="submit">Log Chore</button>
          </div>
        </form>
      {% endif %}
    </div>

    <div class="card">
      <h2>Kid Summary</h2>
      {% if kids|length == 0 %}
        <p>No kids yet.</p>
      {% else %}
        <form method="get" action="{{ url_for('kid_summary') }}">
          <div class="row">
            <select name="kid_name" required>
              {% for k in kids %}
                <option value="{{ k['name'] }}">{{ k['name'] }}</option>
              {% endfor %}
            </select>
            <button class="btn" type="submit">View</button>
          </div>
        </form>
      {% endif %}
    </div>
    """
    body = render_template_string(page, kids=kids, chores=chores, dollars=dollars)
    return render_template_string(BASE, title="Kid", body=body)

@app.post("/kid/log")
def kid_log():
    family_id = ensure_family_ready()
    kid_name = (request.form.get("kid_name") or "").strip()
    chore_key = (request.form.get("chore_key") or "").strip()
    if kid_name and chore_key:
        submit_chore_db(family_id, kid_name, chore_key)
    return redirect(url_for("kid_menu"))

@app.get("/kid/summary")
def kid_summary():
    family_id = ensure_family_ready()
    kid_name = (request.args.get("kid_name") or "").strip()
    if not kid_name:
        return redirect(url_for("kid_menu"))

    kids = kids_rows(family_id)
    kid = next((k for k in kids if k["name"] == kid_name), None)
    if not kid:
        return redirect(url_for("kid_menu"))

    owed = owed_by_kid(family_id).get(kid_name, 0)
    paid_total = paid_total_by_kid(family_id).get(kid_name, 0)
    total_done = totals_by_kid(family_id).get(kid_name, 0)
    streak = streak_for_kid(family_id, kid_name)

    rows = ledger_rows(family_id, kid_name)[:50]

    goal = int(kid["goal_cents"])
    progress = paid_total + owed  # what you've earned (paid + waiting)
    remaining = max(goal - progress, 0)

    page = """
    <h1>Kid Summary: {{ kid_name }}</h1>

    <div class="card">
      <div class="row">
        <div><b>Owed (approved, unpaid):</b> {{ dollars(owed) }}</div>
        <div><b>Paid total:</b> {{ dollars(paid_total) }}</div>
        <div><b>Total chores:</b> {{ total_done }}</div>
        <div><b>Streak:</b> {{ streak }} day(s)</div>
      </div>
    </div>

    <div class="card">
      <h2>Goal</h2>
      <p><b>Goal:</b> {{ dollars(goal) }}</p>
      <p><b>Earned (paid + owed):</b> {{ dollars(progress) }}</p>
      <p><b>Remaining:</b> {{ dollars(remaining) }}</p>
    </div>

    <div class="card">
      <h2>Recent chores</h2>
      {% if rows|length == 0 %}
        <p>No chores yet.</p>
      {% else %}
        <table>
          <tr><th>When</th><th>Chore</th><th>Amount</th><th>Status</th></tr>
          {% for r in rows %}
            <tr>
              <td>{{ dt(r['ts']) }}</td>
              <td>{{ r['chore_title'] }}</td>
              <td>{{ dollars(r['reward_cents']) }}</td>
              <td>{{ r['status'] }}</td>
            </tr>
          {% endfor %}
        </table>
      {% endif %}
    </div>

    <div class="card">
      <a class="btn" href="{{ url_for('kid_menu') }}">Back to Kid Menu</a>
    </div>
    """
    def dt(ts: float) -> str:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")

    body = render_template_string(
        page,
        kid_name=kid_name,
        owed=owed,
        paid_total=paid_total,
        total_done=total_done,
        streak=streak,
        rows=rows,
        dollars=dollars,
        dt=dt,
        goal=goal,
        progress=progress,
        remaining=remaining,
    )
    return render_template_string(BASE, title="Kid Summary", body=body)

@app.get("/parent")
def parent_dashboard():
    require_parent()
    family_id = ensure_family_ready()

    kids = kids_rows(family_id)
    chores = chores_rows(family_id)
    owed = owed_by_kid(family_id)
    totals = totals_by_kid(family_id)
    paid_totals = paid_total_by_kid(family_id)

    # pending list
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM ledger
                WHERE family_id=%s AND status='pending'
                ORDER BY id DESC
            """, (family_id,))
            pending = list(cur.fetchall())

    page = """
    <h1>Parent Dashboard</h1>

    <div class="card">
      <h2>Add a kid</h2>
      <form method="post" action="{{ url_for('parent_add_kid') }}">
        <div class="row">
          <input name="kid_name" placeholder="Kid name" required />
          <button class="btn btn-primary" type="submit">Add</button>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Kids (owed + streaks + goals)</h2>
      {% if kids|length == 0 %}
        <p>No kids yet.</p>
      {% else %}
        <table>
          <tr>
            <th>Kid</th>
            <th>Total chores</th>
            <th>Streak</th>
            <th>Owed</th>
            <th>Paid total</th>
            <th>Goal</th>
            <th>Set goal</th>
            <th>Pay</th>
            <th>Details</th>
          </tr>
          {% for k in kids %}
            {% set name = k['name'] %}
            <tr>
              <td><b>{{ name }}</b></td>
              <td>{{ totals.get(name, 0) }}</td>
              <td>{{ streak(name) }} day(s)</td>
              <td><b>{{ dollars(owed.get(name, 0)) }}</b></td>
              <td>{{ dollars(paid_totals.get(name, 0)) }}</td>
              <td>{{ dollars(k['goal_cents']) }}</td>
              <td>
                <form method="post" action="{{ url_for('parent_set_goal') }}">
                  <input type="hidden" name="kid_name" value="{{ name }}" />
                  <input name="goal_cents" placeholder="cents" style="width:90px;" required />
                  <button class="btn" type="submit">Set</button>
                </form>
              </td>
              <td>
                <form method="post" action="{{ url_for('parent_pay_kid') }}">
                  <input type="hidden" name="kid_name" value="{{ name }}" />
                  <button class="btn" type="submit">Mark paid</button>
                </form>
              </td>
              <td>
                <a class="btn" href="{{ url_for('parent_kid_details', kid_name=name) }}">See chores</a>
              </td>
            </tr>
          {% endfor %}
        </table>
        <p><small>“Owed” = approved chores not marked paid yet.</small></p>
      {% endif %}
    </div>

    <div class="card">
      <h2>Pending approvals</h2>
      {% if pending|length == 0 %}
        <p>No pending chores.</p>
      {% else %}
        <table>
          <tr><th>Kid</th><th>Chore</th><th>Amount</th><th>Action</th></tr>
          {% for r in pending %}
            <tr>
              <td>{{ r['kid_name'] }}</td>
              <td>{{ r['chore_title'] }}</td>
              <td>{{ dollars(r['reward_cents']) }}</td>
              <td class="row">
                <form method="post" action="{{ url_for('parent_approve') }}">
                  <input type="hidden" name="ledger_id" value="{{ r['id'] }}" />
                  <input type="hidden" name="approve" value="1" />
                  <button class="btn btn-primary" type="submit">Approve</button>
                </form>
                <form method="post" action="{{ url_for('parent_approve') }}">
                  <input type="hidden" name="ledger_id" value="{{ r['id'] }}" />
                  <input type="hidden" name="approve" value="0" />
                  <button class="btn btn-danger" type="submit">Deny</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
      {% endif %}
    </div>

    <div class="card">
      <h2>Edit chores & payouts</h2>
      <a class="btn btn-primary" href="{{ url_for('parent_edit_chores') }}">Edit chores</a>
    </div>
    """

    def streak(name: str) -> int:
        return streak_for_kid(family_id, name)

    body = render_template_string(
        page,
        kids=kids,
        chores=chores,
        owed=owed,
        totals=totals,
        paid_totals=paid_totals,
        pending=pending,
        dollars=dollars,
        streak=streak,
    )
    return render_template_string(BASE, title="Parent", body=body)

@app.post("/parent/add_kid")
def parent_add_kid():
    require_parent()
    family_id = ensure_family_ready()
    name = (request.form.get("kid_name") or "").strip()
    add_kid_db(family_id, name)
    return redirect(url_for("parent_dashboard"))

@app.post("/parent/set_goal")
def parent_set_goal():
    require_parent()
    family_id = ensure_family_ready()
    kid_name = (request.form.get("kid_name") or "").strip()
    raw = (request.form.get("goal_cents") or "").strip()
    try:
        goal_cents = int(raw)
    except ValueError:
        goal_cents = 0
    if goal_cents < 0:
        goal_cents = 0
    set_goal_db(family_id, kid_name, goal_cents)
    return redirect(url_for("parent_dashboard"))

@app.post("/parent/pay")
def parent_pay_kid():
    require_parent()
    family_id = ensure_family_ready()
    kid_name = (request.form.get("kid_name") or "").strip()
    mark_paid_db(family_id, kid_name)
    return redirect(url_for("parent_dashboard"))

@app.post("/parent/approve")
def parent_approve():
    require_parent()
    family_id = ensure_family_ready()
    ledger_id_raw = (request.form.get("ledger_id") or "").strip()
    approve = (request.form.get("approve") or "0") == "1"
    try:
        ledger_id = int(ledger_id_raw)
    except ValueError:
        return redirect(url_for("parent_dashboard"))
    approve_deny_db(family_id, ledger_id, approve)
    return redirect(url_for("parent_dashboard"))

@app.get("/parent/kid/<kid_name>")
def parent_kid_details(kid_name: str):
    require_parent()
    family_id = ensure_family_ready()
    kid_name = (kid_name or "").strip()

    rows = ledger_rows(family_id, kid_name)[:200]

    page = """
    <h1>Chores for {{ kid_name }}</h1>

    <div class="card">
      {% if rows|length == 0 %}
        <p>No chores yet.</p>
      {% else %}
        <table>
          <tr><th>When</th><th>Chore</th><th>Amount</th><th>Status</th></tr>
          {% for r in rows %}
            <tr>
              <td>{{ dt(r['ts']) }}</td>
              <td>{{ r['chore_title'] }}</td>
              <td>{{ dollars(r['reward_cents']) }}</td>
              <td>{{ r['status'] }}</td>
            </tr>
          {% endfor %}
        </table>
      {% endif %}
    </div>

    <div class="card">
      <a class="btn" href="{{ url_for('parent_dashboard') }}">Back to Parent</a>
    </div>
    """

    def dt(ts: float) -> str:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")

    body = render_template_string(page, kid_name=kid_name, rows=rows, dollars=dollars, dt=dt)
    return render_template_string(BASE, title="Kid Details", body=body)

@app.get("/parent/chores")
def parent_edit_chores():
    require_parent()
    family_id = ensure_family_ready()
    chores = chores_rows(family_id)

    page = """
    <h1>Edit Chores</h1>

    <div class="card">
      <h2>Add / Update</h2>
      <form method="post" action="{{ url_for('parent_save_chore') }}">
        <div class="row">
          <input name="chore_key" placeholder="id (example: vacuum)" required />
          <input name="title" placeholder="title kids see" required />
          <input name="reward_cents" placeholder="cents (example: 75)" required />
          <select name="requires_approval">
            <option value="1">Needs approval</option>
            <option value="0">Auto-approved</option>
          </select>
          <button class="btn btn-primary" type="submit">Save</button>
        </div>
      </form>
      <p><small>100 cents = $1.00</small></p>
    </div>

    <div class="card">
      <h2>Current chores</h2>
      {% if chores|length == 0 %}
        <p>No chores.</p>
      {% else %}
        <table>
          <tr><th>ID</th><th>Title</th><th>Payout</th><th>Approval</th><th>Delete</th></tr>
          {% for c in chores %}
            <tr>
              <td>{{ c['chore_key'] }}</td>
              <td>{{ c['title'] }}</td>
              <td>{{ dollars(c['reward_cents']) }}</td>
              <td>{% if c['requires_approval'] %}needs approval{% else %}auto{% endif %}</td>
              <td>
                <form method="post" action="{{ url_for('parent_delete_chore') }}">
                  <input type="hidden" name="chore_key" value="{{ c['chore_key'] }}" />
                  <button class="btn btn-danger" type="submit">Delete</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
      {% endif %}
    </div>

    <div class="card">
      <a class="btn" href="{{ url_for('parent_dashboard') }}">Back to Parent</a>
    </div>
    """
    body = render_template_string(page, chores=chores, dollars=dollars)
    return render_template_string(BASE, title="Edit Chores", body=body)

@app.post("/parent/chores/save")
def parent_save_chore():
    require_parent()
    family_id = ensure_family_ready()

    key = (request.form.get("chore_key") or "").strip()
    title = (request.form.get("title") or "").strip()
    raw = (request.form.get("reward_cents") or "0").strip()
    requires = (request.form.get("requires_approval") or "1") == "1"
    try:
        cents = int(raw)
    except ValueError:
        cents = 0
    if cents < 0:
        cents = 0

    add_or_update_chore_db(family_id, key, title, cents, requires)
    return redirect(url_for("parent_edit_chores"))

@app.post("/parent/chores/delete")
def parent_delete_chore():
    require_parent()
    family_id = ensure_family_ready()
    key = (request.form.get("chore_key") or "").strip()
    delete_chore_db(family_id, key)
    return redirect(url_for("parent_edit_chores"))


# ============================================================
# STARTUP
# ============================================================

# Create tables on import (safe)
if DATABASE_OK:
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
