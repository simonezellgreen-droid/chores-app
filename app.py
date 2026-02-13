from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from flask import Flask, request, redirect, url_for, render_template_string, session


# ============================================================
# Config (Render-friendly)
# ============================================================

APP_NAME = "Chores Tracker"
DATA_FILE = os.environ.get("DATA_FILE", "chores_game.json")

# Family gate (keeps strangers out)
FAMILY_CODE = os.environ.get("FAMILY_CODE", "1234")

# Parent role gate (keeps kids from approving/paying/editing)
PARENT_PIN = os.environ.get("PARENT_PIN", "0000")

# Sessions/cookies: MUST be stable across deploys
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-on-render")

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ============================================================
# Helpers
# ============================================================

def dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"


def load_data() -> Dict[str, Any]:
    path = Path(DATA_FILE)
    if not path.exists():
        return {
            "kids": {},         # name -> {"name":..., "goal_cents":...}
            "chores": {},       # id -> {"id":..., "title":..., "reward_cents":..., "requires_approval":...}
            "ledger": []        # list of entries
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_data(data: Dict[str, Any]) -> None:
    Path(DATA_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def safe_chore_id(title: str) -> str:
    # Auto-generate an id from a title: "Vacuum Room" -> "vacuum_room"
    keep = []
    for ch in title.lower().strip():
        if ch.isalnum():
            keep.append(ch)
        elif ch in (" ", "-", "_"):
            keep.append("_")
    cid = "".join(keep)
    while "__" in cid:
        cid = cid.replace("__", "_")
    return cid.strip("_") or "chore"


# ============================================================
# Models
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
class Entry:
    kid: str
    chore_id: str
    chore_title: str
    reward_cents: int
    ts: float
    status: str  # pending | approved | denied | paid


# ============================================================
# App State
# ============================================================

data = load_data()

kids: Dict[str, Kid] = {
    name: Kid(**payload) for name, payload in data.get("kids", {}).items()
}

chores: Dict[str, Chore] = {
    cid: Chore(**payload) for cid, payload in data.get("chores", {}).items()
}

ledger: List[Entry] = [
    Entry(**payload) for payload in data.get("ledger", [])
]


def persist() -> None:
    save_data({
        "kids": {k: asdict(v) for k, v in kids.items()},
        "chores": {c: asdict(v) for c, v in chores.items()},
        "ledger": [asdict(e) for e in ledger],
    })


def seed_defaults_if_needed() -> None:
    if chores:
        return
    defaults = [
        Chore("make_bed", "Make bed", 50, requires_approval=False),
        Chore("dishes", "Unload dishwasher", 100, requires_approval=True),
        Chore("trash", "Take out trash", 75, requires_approval=False),
    ]
    for c in defaults:
        chores[c.id] = c
    persist()


seed_defaults_if_needed()


# ============================================================
# Security gates
# ============================================================

def require_family() -> Optional[Any]:
    if session.get("family_ok"):
        return None
    return redirect(url_for("family_login"))


def require_parent() -> Optional[Any]:
    if not session.get("family_ok"):
        return redirect(url_for("family_login"))
    if session.get("parent_ok"):
        return None
    return redirect(url_for("parent_login"))


# ============================================================
# Templates (simple and reliable)
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
    .top a { margin-right: 10px; }
    .card { border: 1px solid #e5e5e5; border-radius: 14px; padding: 14px; margin: 12px 0; }
    .row { display:flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .btn { display:inline-block; padding: 10px 12px; border-radius: 12px; border: 1px solid #ccc; background: #fafafa; text-decoration: none; color: #111; cursor: pointer; }
    .btn-primary { background:#111; color:#fff; border-color:#111; }
    .btn-danger { background:#fff5f5; border-color:#ffcccc; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #eee; text-align:left; vertical-align: top; }
    input, select { padding: 9px; border-radius: 12px; border: 1px solid #ccc; }
    small { color:#666; }
    ul { margin:0; padding-left: 18px; }
    .muted { color:#777; }
  </style>
</head>
<body>
  <div class="top">
    <a href="{{ url_for('menu') }}">Menu</a>
    <a href="{{ url_for('kid_page') }}">Kid</a>
    <a href="{{ url_for('kid_summary_page') }}">Kid Summary</a>
    <a href="{{ url_for('parent_dashboard') }}">Parent</a>
    <a href="{{ url_for('edit_chores_page') }}">Edit Chores</a>
    <a href="{{ url_for('logout') }}">Log out</a>
  </div>
  <hr />
  {{ body|safe }}
</body>
</html>
"""


# ============================================================
# Routes: Login / Logout
# ============================================================

@app.get("/")
def root():
    # Always send to menu (it will redirect to login if needed)
    return redirect(url_for("menu"))


@app.route("/family", methods=["GET", "POST"])
def family_login():
    error = ""
    if request.method == "POST":
        code = request.form.get("code", "")
        if code == FAMILY_CODE:
            session["family_ok"] = True
            return redirect(url_for("menu"))
        error = "Wrong family code. Try again."
    body = """
      <h1>Family Code</h1>
      <div class="card">
        <p>This app is private. Enter the family code.</p>
        {% if error %}<p style="color:#b00020;"><b>{{ error }}</b></p>{% endif %}
        <form method="post">
          <div class="row">
            <input name="code" placeholder="Family code" required />
            <button class="btn btn-primary" type="submit">Enter</button>
          </div>
          <p class="muted"><small>If you set FAMILY_CODE on Render, use that. Otherwise default is 1234.</small></p>
        </form>
      </div>
    """
    return render_template_string(BASE, title="Family Login", body=render_template_string(body, error=error))


@app.route("/parent_login", methods=["GET", "POST"])
def parent_login():
    if not session.get("family_ok"):
        return redirect(url_for("family_login"))
    error = ""
    if request.method == "POST":
        pin = request.form.get("pin", "")
        if pin == PARENT_PIN:
            session["parent_ok"] = True
            return redirect(url_for("parent_dashboard"))
        error = "Wrong parent PIN."
    body = """
      <h1>Parent PIN</h1>
      <div class="card">
        <p>Parent actions are protected. Enter the parent PIN.</p>
        {% if error %}<p style="color:#b00020;"><b>{{ error }}</b></p>{% endif %}
        <form method="post">
          <div class="row">
            <input name="pin" placeholder="Parent PIN" required />
            <button class="btn btn-primary" type="submit">Enter</button>
          </div>
        </form>
      </div>
    """
    return render_template_string(BASE, title="Parent Login", body=render_template_string(body, error=error))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("family_login"))


# ============================================================
# Menu
# ============================================================

@app.get("/menu")
def menu():
    gate = require_family()
    if gate:
        return gate

    body = """
      <h1>Main Menu</h1>
      <div class="card">
        <div class="row">
          <a class="btn btn-primary" href="{{ url_for('kid_page') }}">Kid: Log a chore</a>
          <a class="btn btn-primary" href="{{ url_for('kid_summary_page') }}">Kid: Summary</a>
          <a class="btn btn-primary" href="{{ url_for('parent_dashboard') }}">Parent: Dashboard</a>
        </div>
        <p><small>Family-only app. Parent actions require the Parent PIN.</small></p>
      </div>
    """
    return render_template_string(BASE, title="Menu", body=render_template_string(body))


# ============================================================
# Kid pages
# ============================================================

@app.get("/kid")
def kid_page():
    gate = require_family()
    if gate:
        return gate

    kid_names = sorted(kids.keys())
    chores_list = sorted(chores.values(), key=lambda c: c.title.lower())

    body = """
      <h1>Kid Menu</h1>

      <div class="card">
        <h2>Log a chore</h2>

        {% if not kid_names %}
          <p>No kids yet. Ask a parent to add you on the Parent Dashboard.</p>
        {% else %}
          <form method="post" action="{{ url_for('kid_log') }}">
            <div class="row">
              <div>
                <div><small>Your name</small></div>
                <select name="kid_name" required>
                  {% for k in kid_names %}
                    <option value="{{k}}">{{k}}</option>
                  {% endfor %}
                </select>
              </div>

              <div>
                <div><small>Chore</small></div>
                <select name="chore_id" required>
                  {% for c in chores_list %}
                    <option value="{{c.id}}">
                      {{c.title}} ({{ dollars(c.reward_cents) }}) {% if c.requires_approval %}[needs approval]{% else %}[auto]{% endif %}
                    </option>
                  {% endfor %}
                </select>
              </div>

              <div style="margin-top:18px;">
                <button class="btn btn-primary" type="submit">Log chore</button>
              </div>
            </div>
          </form>
        {% endif %}
      </div>
    """
    return render_template_string(
        BASE,
        title="Kid",
        body=render_template_string(body, kid_names=kid_names, chores_list=chores_list, dollars=dollars),
    )


@app.post("/kid/log")
def kid_log():
    gate = require_family()
    if gate:
        return gate

    kid_name = request.form.get("kid_name", "").strip()
    chore_id = request.form.get("chore_id", "").strip()

    if kid_name not in kids:
        return redirect(url_for("kid_page"))
    if chore_id not in chores:
        return redirect(url_for("kid_page"))

    chore = chores[chore_id]
    status = "pending" if chore.requires_approval else "approved"

    ledger.append(
        Entry(
            kid=kid_name,
            chore_id=chore.id,
            chore_title=chore.title,
            reward_cents=chore.reward_cents,
            ts=time.time(),
            status=status,
        )
    )
    persist()
    return redirect(url_for("kid_page"))


def calc_streak_days(kid_name: str) -> int:
    # Count consecutive days ending today where kid has at least one non-denied entry.
    days_with_activity = set()
    for e in ledger:
        if e.kid == kid_name and e.status != "denied":
            d = date.fromtimestamp(e.ts)
            days_with_activity.add(d)

    streak = 0
    today = date.today()
    while (today - timedelta(days=streak)) in days_with_activity:
        streak += 1
    return streak


@app.get("/kid/summary")
def kid_summary_page():
    gate = require_family()
    if gate:
        return gate

    kid_names = sorted(kids.keys())
    selected = request.args.get("kid", "")
    if not selected and kid_names:
        selected = kid_names[0]

    entries = [e for e in ledger if e.kid == selected] if selected else []
    entries_sorted = sorted(entries, key=lambda e: e.ts, reverse=True)

    approved_total = sum(e.reward_cents for e in entries if e.status == "approved")
    paid_total = sum(e.reward_cents for e in entries if e.status == "paid")
    total_done = sum(1 for e in entries if e.status in ("pending", "approved", "paid"))
    streak_days = calc_streak_days(selected) if selected else 0
    goal = kids[selected].goal_cents if selected in kids else 0

    body = """
      <h1>Kid Summary</h1>

      <div class="card">
        <form method="get" action="{{ url_for('kid_summary_page') }}">
          <div class="row">
            <div>
              <div><small>Kid</small></div>
              <select name="kid">
                {% for k in kid_names %}
                  <option value="{{k}}" {% if k==selected %}selected{% endif %}>{{k}}</option>
                {% endfor %}
              </select>
            </div>
            <div style="margin-top:18px;">
              <button class="btn" type="submit">View</button>
            </div>
          </div>
        </form>
      </div>

      {% if not selected %}
        <p>No kids yet.</p>
      {% else %}
        <div class="card">
          <h2>{{ selected }}</h2>
          <p><b>Approved (unpaid):</b> {{ dollars(approved_total) }}</p>
          <p><b>Paid total:</b> {{ dollars(paid_total) }}</p>
          <p><b>Total chores logged:</b> {{ total_done }}</p>
          <p><b>Streak:</b> {{ streak_days }} day(s)</p>

          <p><b>Goal:</b> {{ dollars(goal) }}</p>
          <p class="muted"><small>(Parents can set goals in the Parent Dashboard.)</small></p>
        </div>

        <div class="card">
          <h3>Recent chores</h3>
          {% if not entries_sorted %}
            <p>No chores yet.</p>
          {% else %}
            <table>
              <tr><th>Chore</th><th>Amount</th><th>Status</th></tr>
              {% for e in entries_sorted[:30] %}
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
    """
    return render_template_string(
        BASE,
        title="Kid Summary",
        body=render_template_string(
            body,
            kid_names=kid_names,
            selected=selected,
            approved_total=approved_total,
            paid_total=paid_total,
            total_done=total_done,
            streak_days=streak_days,
            goal=goal,
            entries_sorted=entries_sorted,
            dollars=dollars,
        ),
    )


# ============================================================
# Parent dashboard
# ============================================================

@app.get("/parent")
def parent_dashboard():
    gate = require_parent()
    if gate:
        return gate

    kid_names = sorted(kids.keys())
    chores_list = sorted(chores.values(), key=lambda c: c.title.lower())

    # Owed + itemized list
    approved_by_kid: Dict[str, List[Entry]] = {k: [] for k in kid_names}
    for e in ledger:
        if e.status == "approved" and e.kid in approved_by_kid:
            approved_by_kid[e.kid].append(e)

    owed_by_kid: Dict[str, int] = {k: sum(e.reward_cents for e in approved_by_kid[k]) for k in kid_names}

    pending: List[tuple[int, Entry]] = [(i, e) for i, e in enumerate(ledger) if e.status == "pending"]

    body = """
      <h1>Parent Dashboard</h1>

      <div class="card">
        <h2>Add kid</h2>
        <form method="post" action="{{ url_for('parent_add_kid') }}">
          <div class="row">
            <input name="kid_name" placeholder="Kid name" required />
            <button class="btn btn-primary" type="submit">Add</button>
          </div>
        </form>
      </div>

      <div class="card">
        <h2>Set goal</h2>
        {% if not kid_names %}
          <p>No kids yet.</p>
        {% else %}
          <form method="post" action="{{ url_for('parent_set_goal') }}">
            <div class="row">
              <select name="kid_name">
                {% for k in kid_names %}
                  <option value="{{k}}">{{k}}</option>
                {% endfor %}
              </select>
              <input name="goal_cents" placeholder="Goal in cents (e.g. 2500 = $25.00)" required />
              <button class="btn" type="submit">Set goal</button>
            </div>
          </form>
        {% endif %}
      </div>

      <div class="card">
        <h2>Owed</h2>
        {% if not kid_names %}
          <p>No kids yet.</p>
        {% else %}
          <table>
            <tr><th>Kid</th><th>Owe (approved, unpaid)</th><th>What they did</th><th>Pay</th></tr>
            {% for k in kid_names %}
              <tr>
                <td>{{k}}</td>
                <td><b>{{ dollars(owed_by_kid[k]) }}</b></td>
                <td>
                  {% if not approved_by_kid[k] %}
                    <small>—</small>
                  {% else %}
                    <ul>
                      {% for e in approved_by_kid[k] %}
                        <li>{{ e.chore_title }} ({{ dollars(e.reward_cents) }})</li>
                      {% endfor %}
                    </ul>
                  {% endif %}
                </td>
                <td>
                  <form method="post" action="{{ url_for('parent_pay_kid') }}">
                    <input type="hidden" name="kid_name" value="{{k}}" />
                    <button class="btn" type="submit">Mark paid</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
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
                <td>{{ e.kid }}</td>
                <td>{{ e.chore_title }}</td>
                <td>{{ dollars(e.reward_cents) }}</td>
                <td class="row">
                  <form method="post" action="{{ url_for('parent_approve') }}">
                    <input type="hidden" name="idx" value="{{ idx }}" />
                    <input type="hidden" name="action" value="approve" />
                    <button class="btn btn-primary" type="submit">Approve</button>
                  </form>
                  <form method="post" action="{{ url_for('parent_approve') }}">
                    <input type="hidden" name="idx" value="{{ idx }}" />
                    <input type="hidden" name="action" value="deny" />
                    <button class="btn btn-danger" type="submit">Deny</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% endif %}
      </div>

      <div class="card">
        <h2>Go to Edit Chores</h2>
        <a class="btn" href="{{ url_for('edit_chores_page') }}">Edit chores & payouts</a>
      </div>
    """
    return render_template_string(
        BASE,
        title="Parent",
        body=render_template_string(
            body,
            kid_names=kid_names,
            owed_by_kid=owed_by_kid,
            approved_by_kid=approved_by_kid,
            pending=pending,
            dollars=dollars,
        ),
    )


@app.post("/parent/add_kid")
def parent_add_kid():
    gate = require_parent()
    if gate:
        return gate
    name = request.form.get("kid_name", "").strip()
    if name and name not in kids:
        kids[name] = Kid(name=name, goal_cents=0)
        persist()
    return redirect(url_for("parent_dashboard"))


@app.post("/parent/set_goal")
def parent_set_goal():
    gate = require_parent()
    if gate:
        return gate
    kid_name = request.form.get("kid_name", "").strip()
    goal_raw = request.form.get("goal_cents", "0").strip()
    try:
        goal = int(goal_raw)
    except ValueError:
        goal = 0
    if kid_name in kids and goal >= 0:
        kids[kid_name].goal_cents = goal
        persist()
    return redirect(url_for("parent_dashboard"))


@app.post("/parent/approve")
def parent_approve():
    gate = require_parent()
    if gate:
        return gate
    idx_raw = request.form.get("idx", "-1")
    action = request.form.get("action", "")
    try:
        idx = int(idx_raw)
    except ValueError:
        idx = -1
    if 0 <= idx < len(ledger):
        if ledger[idx].status == "pending":
            ledger[idx].status = "approved" if action == "approve" else "denied"
            persist()
    return redirect(url_for("parent_dashboard"))


@app.post("/parent/pay")
def parent_pay_kid():
    gate = require_parent()
    if gate:
        return gate
    kid_name = request.form.get("kid_name", "").strip()
    if kid_name:
        for e in ledger:
            if e.kid == kid_name and e.status == "approved":
                e.status = "paid"
        persist()
    return redirect(url_for("parent_dashboard"))


# ============================================================
# Edit chores (Parent-only)
# ============================================================

@app.get("/parent/edit_chores")
def edit_chores_page():
    gate = require_parent()
    if gate:
        return gate

    chores_list = sorted(chores.values(), key=lambda c: c.title.lower())

    body = """
      <h1>Edit Chores & Payouts</h1>

      <div class="card">
        <h2>Add / Update chore</h2>
        <form method="post" action="{{ url_for('save_chore') }}">
          <div class="row">
            <input name="title" placeholder="Title (e.g. Vacuum room)" required />
            <input name="reward_cents" placeholder="Reward cents (e.g. 75)" required />
            <select name="requires_approval">
              <option value="1">Needs approval</option>
              <option value="0">Auto-approved</option>
            </select>
            <button class="btn btn-primary" type="submit">Save</button>
          </div>
          <p class="muted"><small>ID is auto-created from the title.</small></p>
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
                    <input type="hidden" name="chore_id" value="{{ c.id }}" />
                    <button class="btn btn-danger" type="submit">Delete</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% endif %}
      </div>
    """
    return render_template_string(
        BASE,
        title="Edit Chores",
        body=render_template_string(body, chores_list=chores_list, dollars=dollars),
    )


@app.post("/parent/edit_chores/save")
def save_chore():
    gate = require_parent()
    if gate:
        return gate

    title = request.form.get("title", "").strip()
    reward_raw = request.form.get("reward_cents", "").strip()
    requires_approval = request.form.get("requires_approval", "1") == "1"

    try:
        reward = int(reward_raw)
    except ValueError:
        reward = 0

    if not title:
        return redirect(url_for("edit_chores_page"))

    cid = safe_chore_id(title)
    chores[cid] = Chore(id=cid, title=title, reward_cents=max(0, reward), requires_approval=requires_approval)
    persist()
    return redirect(url_for("edit_chores_page"))


@app.post("/parent/edit_chores/delete")
def delete_chore():
    gate = require_parent()
    if gate:
        return gate

    cid = request.form.get("chore_id", "").strip()
    if cid in chores:
        del chores[cid]
        persist()
    return redirect(url_for("edit_chores_page"))


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
