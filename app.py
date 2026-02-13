from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from flask import Flask, request, redirect, url_for, render_template_string, session

# ============================================================
# CONFIG (Render-friendly)
# ============================================================

APP_NAME = "Chores Tracker"
DATA_FILE = os.environ.get("DATA_FILE", "chores_game.json")

# ✅ Family code keeps strangers out (set this on Render > Environment)
FAMILY_CODE = os.environ.get("FAMILY_CODE", "1234")

# ✅ Parent PIN protects parent-only actions (set this on Render > Environment)
PARENT_PIN = os.environ.get("PARENT_PIN", "0000")

# ✅ Secret key needed for sessions (set this on Render > Environment)
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ============================================================
# MODELS
# ============================================================

@dataclass
class Kid:
    name: str
    goal_cents: int = 0


@dataclass
class Chore:
    id: str
    title: str
    reward_cents: int
    requires_approval: bool = True


@dataclass
class LedgerEntry:
    kid_name: str
    chore_id: str
    chore_title: str
    reward_cents: int
    ts: float
    status: str  # pending | approved | denied | paid


# ============================================================
# HELPERS
# ============================================================

def dollars(cents: int) -> str:
    return f"${cents/100:.2f}"


def safe_id(s: str) -> str:
    s = s.strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    cid = "".join(out)
    while "__" in cid:
        cid = cid.replace("__", "_")
    return cid.strip("_") or "chore"


def ts_to_date(ts: float) -> date:
    return date.fromtimestamp(ts)


def calc_streak(kid: str, ledger: List[LedgerEntry]) -> int:
    done_days = set()
    for e in ledger:
        if e.kid_name == kid and e.status != "denied":
            done_days.add(ts_to_date(e.ts))
    streak = 0
    d = date.today()
    while d in done_days:
        streak += 1
        d = d - timedelta(days=1)
    return streak


# ============================================================
# STORAGE
# ============================================================

class Store:
    def __init__(self, path: str = DATA_FILE):
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"kids": {}, "chores": {}, "ledger": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ============================================================
# GAME LOGIC
# ============================================================

class Game:
    def __init__(self, store: Store):
        self.store = store
        self.data = self.store.load()

        self.kids: Dict[str, Kid] = {
            name: Kid(**kid_dict) for name, kid_dict in self.data.get("kids", {}).items()
        }

        self.chores: Dict[str, Chore] = {
            cid: Chore(**c) for cid, c in self.data.get("chores", {}).items()
        }

        self.ledger: List[LedgerEntry] = [
            LedgerEntry(**e) for e in self.data.get("ledger", [])
        ]

        if not self.chores:
            self.seed_defaults()
            self.persist()

    def persist(self) -> None:
        self.data["kids"] = {k: asdict(v) for k, v in self.kids.items()}
        self.data["chores"] = {cid: asdict(v) for cid, v in self.chores.items()}
        self.data["ledger"] = [asdict(e) for e in self.ledger]
        self.store.save(self.data)

    def seed_defaults(self) -> None:
        defaults = [
            Chore("make_bed", "Make bed", 50, requires_approval=False),
            Chore("dishes", "Unload dishwasher", 100, requires_approval=True),
            Chore("trash", "Take out trash", 75, requires_approval=False),
            Chore("homework", "Homework (checked)", 100, requires_approval=True),
        ]
        for c in defaults:
            self.chores[c.id] = c

    # Kids
    def add_kid(self, name: str) -> bool:
        name = name.strip()
        if not name:
            return False
        if name in self.kids:
            return False
        self.kids[name] = Kid(name=name, goal_cents=0)
        self.persist()
        return True

    def set_goal(self, kid_name: str, goal_cents: int) -> None:
        if kid_name not in self.kids:
            return
        if goal_cents < 0:
            goal_cents = 0
        self.kids[kid_name].goal_cents = goal_cents
        self.persist()

    # Chores
    def add_or_update_chore(self, title: str, reward_cents: int, requires_approval: bool) -> None:
        title = title.strip()
        if not title:
            return
        cid = safe_id(title)
        self.chores[cid] = Chore(cid, title, max(0, reward_cents), requires_approval)
        self.persist()

    def delete_chore(self, cid: str) -> None:
        if cid in self.chores:
            del self.chores[cid]
            self.persist()

    # Ledger
    def submit_chore(self, kid_name: str, chore_id: str) -> None:
        if kid_name not in self.kids:
            return
        if chore_id not in self.chores:
            return
        c = self.chores[chore_id]
        status = "pending" if c.requires_approval else "approved"
        self.ledger.append(
            LedgerEntry(
                kid_name=kid_name,
                chore_id=c.id,
                chore_title=c.title,
                reward_cents=c.reward_cents,
                ts=time.time(),
                status=status,
            )
        )
        self.persist()

    def pending(self) -> List[Tuple[int, LedgerEntry]]:
        return [(i, e) for i, e in enumerate(self.ledger) if e.status == "pending"]

    def approve_or_deny(self, idx: int, approve: bool) -> None:
        if idx < 0 or idx >= len(self.ledger):
            return
        e = self.ledger[idx]
        if e.status != "pending":
            return
        e.status = "approved" if approve else "denied"
        self.persist()

    def mark_paid_for_kid(self, kid_name: str) -> int:
        total = 0
        for e in self.ledger:
            if e.kid_name == kid_name and e.status == "approved":
                e.status = "paid"
                total += e.reward_cents
        self.persist()
        return total

    # Summaries
    def owed_by_kid(self) -> Dict[str, int]:
        owed = {k: 0 for k in self.kids.keys()}
        for e in self.ledger:
            if e.status == "approved":
                owed[e.kid_name] = owed.get(e.kid_name, 0) + e.reward_cents
        return owed

    def lifetime_earned_by_kid(self) -> Dict[str, int]:
        earned = {k: 0 for k in self.kids.keys()}
        for e in self.ledger:
            if e.status in ("approved", "paid"):
                earned[e.kid_name] = earned.get(e.kid_name, 0) + e.reward_cents
        return earned

    def total_chores_by_kid(self) -> Dict[str, int]:
        counts = {k: 0 for k in self.kids.keys()}
        for e in self.ledger:
            if e.status != "denied":
                counts[e.kid_name] = counts.get(e.kid_name, 0) + 1
        return counts

    def chores_list_for_kid(self, kid_name: str, limit: int = 50) -> List[LedgerEntry]:
        items = [e for e in self.ledger if e.kid_name == kid_name]
        items.sort(key=lambda x: x.ts, reverse=True)
        return items[:limit]


game = Game(Store(DATA_FILE))


# ============================================================
# AUTH (Family + Parent)
# ============================================================

def require_family():
    if session.get("family_ok"):
        return None
    return redirect(url_for("family_login"))


def require_parent():
    if not session.get("family_ok"):
        return redirect(url_for("family_login"))
    if session.get("parent_ok"):
        return None
    return redirect(url_for("parent_login"))


# ============================================================
# HTML RENDERING (fixes the {{ }} bug)
# ============================================================

BASE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{{ title }}</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 18px; max-width: 980px; }
    nav a { margin-right: 10px; }
    .card { border: 1px solid #e7e7e7; border-radius: 14px; padding: 14px; margin: 12px 0; }
    .row { display:flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .btn { padding: 10px 12px; border-radius: 12px; border: 1px solid #ccc; background:#fafafa; cursor:pointer; }
    .btn-primary { background:#111; color:#fff; border-color:#111; }
    .btn-danger { background:#fff5f5; border-color:#ffcccc; }
    input, select { padding: 10px; border-radius: 12px; border: 1px solid #ccc; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #eee; text-align:left; vertical-align: top; }
    small { color:#666; }
    .pill { display:inline-block; padding: 2px 8px; border-radius: 999px; border:1px solid #ddd; font-size: 12px; }
  </style>
</head>
<body>
  <nav>
    <a href="{{ url_for('home') }}">Home</a>
    <a href="{{ url_for('kid_menu') }}">Kid</a>
    <a href="{{ url_for('kid_summary') }}">Kid Summary</a>
    <a href="{{ url_for('parent_dash') }}">Parent</a>
    <a href="{{ url_for('edit_chores') }}">Edit Chores</a>
    <a href="{{ url_for('logout') }}">Logout</a>
  </nav>
  <hr/>
  {{ body|safe }}
</body>
</html>
"""


def render_page(title: str, body_template: str, **ctx) -> str:
    body_html = render_template_string(body_template, **ctx)
    return render_template_string(BASE, title=title, body=body_html)


# ============================================================
# ROUTES: Login / Logout
# ============================================================

@app.route("/family", methods=["GET", "POST"])
@app.route("/family/", methods=["GET", "POST"])
def family_login():
    if request.method == "POST":
        code = request.form.get("family_code", "").strip()
        if code == FAMILY_CODE:
            session["family_ok"] = True
            session.pop("parent_ok", None)
            return redirect(url_for("home"))
        return render_page("Family Login", """
            <h1>Family Login</h1>
            <p style="color:red;">Wrong family code.</p>
            <form method="post" action="/family">
              <input name="family_code" placeholder="Family Code" required>
              <button class="btn btn-primary" type="submit">Enter</button>
            </form>
            <p><small>Tip: if you did not set a code on Render, the default is 1234.</small></p>
        """)
    return render_page("Family Login", """
        <h1>Family Login</h1>
        <form method="post" action="/family">
          <input name="family_code" placeholder="Family Code" required>
          <button class="btn btn-primary" type="submit">Enter</button>
        </form>
        <p><small>Tip: if you did not set a code on Render, the default is 1234.</small></p>
    """)


@app.route("/parent_login", methods=["GET", "POST"])
@app.route("/parent_login/", methods=["GET", "POST"])
def parent_login():
    gate = require_family()
    if gate:
        return gate

    if request.method == "POST":
        pin = request.form.get("parent_pin", "").strip()
        if pin == PARENT_PIN:
            session["parent_ok"] = True
            return redirect(url_for("parent_dash"))
        return render_page("Parent Login", """
            <h1>Parent Login</h1>
            <p style="color:red;">Wrong parent PIN.</p>
            <form method="post" action="/parent_login">
              <input name="parent_pin" placeholder="Parent PIN" required>
              <button class="btn btn-primary" type="submit">Enter</button>
            </form>
        """)
    return render_page("Parent Login", """
        <h1>Parent Login</h1>
        <form method="post" action="/parent_login">
          <input name="parent_pin" placeholder="Parent PIN" required>
          <button class="btn btn-primary" type="submit">Enter</button>
        </form>
    """)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("family_login"))


# ============================================================
# ROUTES: Home (Main Menu)
# ============================================================

@app.get("/")
def home():
    gate = require_family()
    if gate:
        return gate

    return render_page("Home", """
      <h1>{{ app_name }}</h1>
      <div class="card">
        <h2>Main Menu</h2>
        <div class="row">
          <a class="btn btn-primary" href="{{ url_for('kid_menu') }}">Kid Menu</a>
          <a class="btn btn-primary" href="{{ url_for('kid_summary') }}">Kid Summary</a>
          <a class="btn btn-primary" href="{{ url_for('parent_dash') }}">Parent Dashboard</a>
        </div>
        <p><small>Parents: you’ll be asked for the Parent PIN the first time you open the parent pages.</small></p>
      </div>
    """, app_name=APP_NAME)


# ============================================================
# ROUTES: Kid Menu + Log Chore
# ============================================================

@app.get("/kid")
def kid_menu():
    gate = require_family()
    if gate:
        return gate

    kids_list = sorted(game.kids.keys())
    chores_list = sorted(game.chores.values(), key=lambda c: c.title.lower())

    return render_page("Kid Menu", """
      <h1>Kid Menu</h1>

      <div class="card">
        <h2>Log a chore</h2>

        {% if not kids_list %}
          <p>No kids exist yet. Ask a parent to add one in the Parent Dashboard.</p>
        {% else %}
          <form method="post" action="{{ url_for('kid_log') }}">
            <div class="row">
              <select name="kid_name" required>
                {% for k in kids_list %}
                  <option value="{{k}}">{{k}}</option>
                {% endfor %}
              </select>

              <select name="chore_id" required>
                {% for c in chores_list %}
                  <option value="{{c.id}}">
                    {{ c.title }} ({{ dollars(c.reward_cents) }}) {% if c.requires_approval %}[needs approval]{% else %}[auto]{% endif %}
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
        <p><small>Pick your name to see your chores, money, streak, and goal progress.</small></p>
        {% if kids_list %}
          <form method="get" action="{{ url_for('kid_summary') }}">
            <div class="row">
              <select name="kid_name" required>
                {% for k in kids_list %}
                  <option value="{{k}}">{{k}}</option>
                {% endfor %}
              </select>
              <button class="btn" type="submit">View</button>
            </div>
          </form>
        {% endif %}
      </div>
    """, kids_list=kids_list, chores_list=chores_list, dollars=dollars)


@app.post("/kid/log")
def kid_log():
    gate = require_family()
    if gate:
        return gate

    kid_name = request.form.get("kid_name", "").strip()
    chore_id = request.form.get("chore_id", "").strip()
    game.submit_chore(kid_name, chore_id)
    return redirect(url_for("kid_menu"))


# ============================================================
# ROUTES: Kid Summary (your own chores + money + streak + goal)
# ============================================================

@app.get("/kid/summary")
def kid_summary():
    gate = require_family()
    if gate:
        return gate

    kids_list = sorted(game.kids.keys())
    kid_name = request.args.get("kid_name", "").strip()

    owed = game.owed_by_kid()
    earned = game.lifetime_earned_by_kid()
    totals = game.total_chores_by_kid()

    chosen = kid_name if kid_name in game.kids else (kids_list[0] if kids_list else "")

    entries = game.chores_list_for_kid(chosen, limit=30) if chosen else []
    streak = calc_streak(chosen, game.ledger) if chosen else 0
    goal_cents = game.kids[chosen].goal_cents if chosen else 0

    return render_page("Kid Summary", """
      <h1>Kid Summary</h1>

      {% if not kids_list %}
        <p>No kids exist yet. Ask a parent to add one.</p>
      {% else %}
        <div class="card">
          <form method="get" action="{{ url_for('kid_summary') }}">
            <div class="row">
              <select name="kid_name">
                {% for k in kids_list %}
                  <option value="{{k}}" {% if k==chosen %}selected{% endif %}>{{k}}</option>
                {% endfor %}
              </select>
              <button class="btn" type="submit">Switch Kid</button>
            </div>
          </form>
        </div>

        <div class="card">
          <h2>{{ chosen }}</h2>
          <div class="row">
            <div><span class="pill">Streak: {{ streak }} day(s)</span></div>
            <div><span class="pill">Total chores: {{ totals.get(chosen,0) }}</span></div>
            <div><span class="pill">Owed: {{ dollars(owed.get(chosen,0)) }}</span></div>
            <div><span class="pill">Lifetime earned: {{ dollars(earned.get(chosen,0)) }}</span></div>
          </div>

          <h3>Goal</h3>
          <p>
            Goal: <b>{{ dollars(goal_cents) }}</b>
            {% if goal_cents > 0 %}
              — Progress (lifetime earned): <b>{{ dollars(earned.get(chosen,0)) }}</b>
            {% endif %}
          </p>
          <form method="post" action="{{ url_for('set_goal') }}">
            <input type="hidden" name="kid_name" value="{{ chosen }}">
            <div class="row">
              <input name="goal_cents" placeholder="Goal cents (example: 500 = $5)" required>
              <button class="btn" type="submit">Set Goal</button>
            </div>
            <p><small>Parents can help set goals too. This is safe (doesn't change money).</small></p>
          </form>
        </div>

        <div class="card">
          <h3>Recent chores</h3>
          {% if not entries %}
            <p>No chores yet.</p>
          {% else %}
            <table>
              <tr><th>Chore</th><th>Amount</th><th>Status</th></tr>
              {% for e in entries %}
                <tr>
                  <td>{{ e.chore_title }}</td>
                  <td>{{ dollars(e.reward_cents) }}</td>
                  <td>{{ e.status }}</td>
                </tr>
              {% endfor %}
            </table>
          {% endif %}
        </div>
      {% endif %}
    """,
    kids_list=kids_list,
    chosen=chosen,
    entries=entries,
    streak=streak,
    owed=owed,
    earned=earned,
    totals=totals,
    goal_cents=goal_cents,
    dollars=dollars
    )


@app.post("/kid/goal")
def set_goal():
    gate = require_family()
    if gate:
        return gate
    kid_name = request.form.get("kid_name", "").strip()
    raw = request.form.get("goal_cents", "0").strip()
    try:
        goal = int(raw)
    except ValueError:
        goal = 0
    game.set_goal(kid_name, goal)
    return redirect(url_for("kid_summary", kid_name=kid_name))


# ============================================================
# ROUTES: Parent Dashboard (owed + exactly which chores + approvals)
# ============================================================

@app.get("/parent")
def parent_dash():
    gate = require_parent()
    if gate:
        return gate

    kids_list = sorted(game.kids.keys())
    owed = game.owed_by_kid()
    totals = game.total_chores_by_kid()
    pending = game.pending()

    # last 50 entries with index
    recent = list(enumerate(game.ledger))[-50:]
    recent.reverse()

    # per-kid list of recent chores (approved/pending/paid; not denied)
    per_kid: Dict[str, List[LedgerEntry]] = {k: [] for k in kids_list}
    for e in sorted(game.ledger, key=lambda x: x.ts, reverse=True):
        if e.status == "denied":
            continue
        if e.kid_name in per_kid and len(per_kid[e.kid_name]) < 15:
            per_kid[e.kid_name].append(e)

    return render_page("Parent Dashboard", """
      <h1>Parent Dashboard</h1>

      <div class="card">
        <h2>Kids + Money Owed</h2>
        {% if not kids_list %}
          <p>No kids yet. Add one below.</p>
        {% else %}
          <table>
            <tr><th>Kid</th><th>Total chores</th><th>Owed (approved, unpaid)</th><th>Pay</th></tr>
            {% for k in kids_list %}
              <tr>
                <td>{{ k }}</td>
                <td>{{ totals.get(k,0) }}</td>
                <td><b>{{ dollars(owed.get(k,0)) }}</b></td>
                <td>
                  <form method="post" action="{{ url_for('pay_kid') }}">
                    <input type="hidden" name="kid_name" value="{{k}}">
                    <button class="btn" type="submit">Mark paid</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
          <p><small>“Owed” = sum of APPROVED chores not yet PAID.</small></p>
        {% endif %}
      </div>

      <div class="card">
        <h2>Pending approvals</h2>
        {% if not pending %}
          <p>No pending chores.</p>
        {% else %}
          <table>
            <tr><th>Kid</th><th>Chore</th><th>Amount</th><th>Action</th></tr>
            {% for idx, e in pending %}
              <tr>
                <td>{{ e.kid_name }}</td>
                <td>{{ e.chore_title }}</td>
                <td>{{ dollars(e.reward_cents) }}</td>
                <td class="row">
                  <form method="post" action="{{ url_for('approve') }}">
                    <input type="hidden" name="idx" value="{{idx}}">
                    <input type="hidden" name="approve" value="1">
                    <button class="btn btn-primary" type="submit">Approve</button>
                  </form>
                  <form method="post" action="{{ url_for('approve') }}">
                    <input type="hidden" name="idx" value="{{idx}}">
                    <input type="hidden" name="approve" value="0">
                    <button class="btn btn-danger" type="submit">Deny</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% endif %}
      </div>

      <div class="card">
        <h2>Exactly what each kid did (recent)</h2>
        {% if not kids_list %}
          <p>Add kids first.</p>
        {% else %}
          {% for k in kids_list %}
            <h3>{{k}}</h3>
            {% if not per_kid.get(k) %}
              <p><small>No chores yet.</small></p>
            {% else %}
              <table>
                <tr><th>Chore</th><th>Amount</th><th>Status</th></tr>
                {% for e in per_kid.get(k) %}
                  <tr>
                    <td>{{ e.chore_title }}</td>
                    <td>{{ dollars(e.reward_cents) }}</td>
                    <td>{{ e.status }}</td>
                  </tr>
                {% endfor %}
              </table>
            {% endif %}
          {% endfor %}
        {% endif %}
      </div>

      <div class="card">
        <h2>Add a kid</h2>
        <form method="post" action="{{ url_for('add_kid') }}">
          <div class="row">
            <input name="kid_name" placeholder="Kid name" required>
            <button class="btn btn-primary" type="submit">Add kid</button>
          </div>
        </form>
      </div>

      <div class="card">
        <h2>Recent activity (last 50)</h2>
        {% if not recent %}
          <p>No activity yet.</p>
        {% else %}
          <table>
            <tr><th>Kid</th><th>Chore</th><th>Amount</th><th>Status</th></tr>
            {% for idx, e in recent %}
              <tr>
                <td>{{ e.kid_name }}</td>
                <td>{{ e.chore_title }}</td>
                <td>{{ dollars(e.reward_cents) }}</td>
                <td>{{ e.status }}</td>
              </tr>
            {% endfor %}
          </table>
        {% endif %}
      </div>
    """,
    kids_list=kids_list,
    owed=owed,
    totals=totals,
    pending=pending,
    per_kid=per_kid,
    recent=recent,
    dollars=dollars
    )


@app.post("/parent/add_kid")
def add_kid():
    gate = require_parent()
    if gate:
        return gate
    kid_name = request.form.get("kid_name", "").strip()
    game.add_kid(kid_name)
    return redirect(url_for("parent_dash"))


@app.post("/parent/approve")
def approve():
    gate = require_parent()
    if gate:
        return gate
    idx = int(request.form.get("idx", "-1"))
    approve_val = request.form.get("approve", "0") == "1"
    game.approve_or_deny(idx, approve_val)
    return redirect(url_for("parent_dash"))


@app.post("/parent/pay")
def pay_kid():
    gate = require_parent()
    if gate:
        return gate
    kid_name = request.form.get("kid_name", "").strip()
    game.mark_paid_for_kid(kid_name)
    return redirect(url_for("parent_dash"))


# ============================================================
# ROUTES: Edit Chores & Payouts
# ============================================================

@app.get("/parent/edit_chores")
def edit_chores():
    gate = require_parent()
    if gate:
        return gate

    chores_list = sorted(game.chores.values(), key=lambda c: c.title.lower())

    return render_page("Edit Chores", """
      <h1>Edit Chores & Payouts</h1>

      <div class="card">
        <h2>Add / Update chore</h2>
        <form method="post" action="{{ url_for('save_chore') }}">
          <div class="row">
            <input name="title" placeholder="Chore title (example: Vacuum)" required>
            <input name="reward_cents" placeholder="Reward cents (example: 75)" required>
            <select name="requires_approval">
              <option value="1">Needs approval</option>
              <option value="0">Auto-approved</option>
            </select>
            <button class="btn btn-primary" type="submit">Save</button>
          </div>
          <p><small>Tip: 100 cents = $1.00</small></p>
        </form>
      </div>

      <div class="card">
        <h2>Current chores</h2>
        {% if not chores_list %}
          <p>No chores yet.</p>
        {% else %}
          <table>
            <tr><th>ID</th><th>Title</th><th>Payout</th><th>Approval</th><th>Delete</th></tr>
            {% for c in chores_list %}
              <tr>
                <td>{{ c.id }}</td>
                <td>{{ c.title }}</td>
                <td>{{ dollars(c.reward_cents) }}</td>
                <td>{% if c.requires_approval %}needs approval{% else %}auto{% endif %}</td>
                <td>
                  <form method="post" action="{{ url_for('delete_chore') }}">
                    <input type="hidden" name="chore_id" value="{{ c.id }}">
                    <button class="btn btn-danger" type="submit">Delete</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% endif %}
      </div>

      <div class="card">
        <a class="btn" href="{{ url_for('parent_dash') }}">← Back to Parent</a>
      </div>
    """, chores_list=chores_list, dollars=dollars)


@app.post("/parent/edit_chores/save")
def save_chore():
    gate = require_parent()
    if gate:
        return gate

    title = request.form.get("title", "").strip()
    raw = request.form.get("reward_cents", "0").strip()
    requires = request.form.get("requires_approval", "1") == "1"
    try:
        reward = int(raw)
    except ValueError:
        reward = 0

    game.add_or_update_chore(title, reward, requires)
    return redirect(url_for("edit_chores"))


@app.post("/parent/edit_chores/delete")
def delete_chore():
    gate = require_parent()
    if gate:
        return gate

    cid = request.form.get("chore_id", "").strip()
    game.delete_chore(cid)
    return redirect(url_for("edit_chores"))


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
