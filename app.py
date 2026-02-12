from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from flask import Flask, request, redirect, url_for, render_template_string

app = Flask(__name__)

DATA_FILE = "chores_game.json"


# ============================================================
# Models
# ============================================================

@dataclass
class Kid:
    name: str


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
    status: str  # "pending" | "denied" | "approved" | "paid"


def dollars(cents: int) -> str:
    return f"${cents/100:.2f}"


# ============================================================
# Persistence
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
# Core Logic
# ============================================================

class ChoresGame:
    def __init__(self, store: Store):
        self.store = store
        self.data = self.store.load()

        # Kids: be tolerant of old schemas
        self.kids: Dict[str, Kid] = {
            name: Kid(name=(kid_dict.get("name") or name))
            for name, kid_dict in self.data.get("kids", {}).items()
        }

        self.chores: Dict[str, Chore] = {
            cid: Chore(**c) for cid, c in self.data.get("chores", {}).items()
        }

        self.ledger: List[LedgerEntry] = [
            LedgerEntry(**e) for e in self.data.get("ledger", [])
        ]

        if not self.chores:
            self._seed_default_chores()
            self._persist()

    def _seed_default_chores(self) -> None:
        defaults = [
            Chore("make_bed", "Make bed", 50, requires_approval=False),
            Chore("dishes", "Unload dishwasher", 100, requires_approval=True),
            Chore("trash", "Take out trash", 75, requires_approval=False),
            Chore("homework", "Homework (checked)", 100, requires_approval=True),
        ]
        for c in defaults:
            self.chores[c.id] = c

    def _persist(self) -> None:
        self.data["kids"] = {name: asdict(k) for name, k in self.kids.items()}
        self.data["chores"] = {cid: asdict(c) for cid, c in self.chores.items()}
        self.data["ledger"] = [asdict(e) for e in self.ledger]
        self.store.save(self.data)

    # Kids
    def add_kid(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        if name in self.kids:
            return
        self.kids[name] = Kid(name=name)
        self._persist()

    # Chores
    def add_or_update_chore(self, chore: Chore) -> None:
        if not chore.id.strip() or not chore.title.strip():
            return
        if chore.reward_cents < 0:
            return
        self.chores[chore.id] = chore
        self._persist()

    def delete_chore(self, chore_id: str) -> None:
        if chore_id in self.chores:
            del self.chores[chore_id]
            self._persist()

    # Ledger
    def submit_chore(self, kid_name: str, chore_id: str) -> None:
        if kid_name not in self.kids:
            return
        if chore_id not in self.chores:
            return
        chore = self.chores[chore_id]
        status = "pending" if chore.requires_approval else "approved"
        self.ledger.append(
            LedgerEntry(
                kid_name=kid_name,
                chore_id=chore.id,
                chore_title=chore.title,
                reward_cents=chore.reward_cents,
                ts=time.time(),
                status=status,
            )
        )
        self._persist()

    def approve_or_deny(self, idx: int, approve: bool) -> None:
        if idx < 0 or idx >= len(self.ledger):
            return
        e = self.ledger[idx]
        if e.status != "pending":
            return
        e.status = "approved" if approve else "denied"
        self._persist()

    def mark_paid_for_kid(self, kid_name: str) -> int:
        total = 0
        for e in self.ledger:
            if e.kid_name == kid_name and e.status == "approved":
                e.status = "paid"
                total += e.reward_cents
        self._persist()
        return total

    # Summaries
    def owed_by_kid(self) -> Dict[str, int]:
        owed = {name: 0 for name in self.kids.keys()}
        for e in self.ledger:
            if e.status == "approved":
                owed[e.kid_name] = owed.get(e.kid_name, 0) + e.reward_cents
        return owed

    def chores_count_by_kid(self) -> Dict[str, int]:
        counts = {name: 0 for name in self.kids.keys()}
        for e in self.ledger:
            if e.status in ("pending", "approved", "paid"):
                counts[e.kid_name] = counts.get(e.kid_name, 0) + 1
        return counts

    def pending(self) -> List[Tuple[int, LedgerEntry]]:
        return [(i, e) for i, e in enumerate(self.ledger) if e.status == "pending"]


game = ChoresGame(Store(DATA_FILE))


# ============================================================
# UI
# ============================================================

BASE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title }}</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 18px; max-width: 920px; }
    .top a { margin-right: 10px; }
    .card { border: 1px solid #e5e5e5; border-radius: 14px; padding: 14px; margin: 12px 0; }
    .row { display:flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .btn { display:inline-block; padding: 10px 12px; border-radius: 12px; border: 1px solid #ccc; background: #fafafa; text-decoration: none; color: #111; }
    .btn-primary { background:#111; color:#fff; border-color:#111; }
    .btn-danger { background:#fff5f5; border-color:#ffcccc; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #eee; text-align:left; }
    input, select { padding: 9px; border-radius: 12px; border: 1px solid #ccc; }
    small { color:#666; }
  </style>
</head>
<body>
  <div class="top">
    <a href="{{ url_for('home') }}">Home</a>
    <a href="{{ url_for('kid') }}">Kid</a>
    <a href="{{ url_for('parent') }}">Parent</a>
    <a href="{{ url_for('edit_chores') }}">Edit Chores</a>
  </div>
  <hr />
  {{ body|safe }}
</body>
</html>
"""


@app.get("/")
def home():
    body = """
    <h1>Chores Tracker</h1>
    <div class="card">
      <h2>Main Menu</h2>
      <div class="row">
        <a class="btn btn-primary" href="{{ url_for('kid') }}">Kid: Log a chore</a>
        <a class="btn btn-primary" href="{{ url_for('parent') }}">Parent: Dashboard</a>
      </div>
      <p><small>This is the website version of your numbered menus: same actions, just buttons.</small></p>
    </div>
    """
    return render_template_string(BASE, title="Home", body=render_template_string(body))


@app.get("/kid")
def kid():
    kids = sorted(game.kids.keys())
    chores = sorted(game.chores.values(), key=lambda c: c.title.lower())

    body = """
    <h1>Kid Menu</h1>

    <div class="card">
      <h2>Log a chore</h2>

      {% if not kids %}
        <p>No kids exist yet. Ask a parent to add one.</p>
      {% else %}
        <form method="post" action="{{ url_for('kid_log') }}">
          <div class="row">
            <div>
              <div><small>Your name</small></div>
              <select name="kid_name" required>
                {% for k in kids %}
                  <option value="{{k}}">{{k}}</option>
                {% endfor %}
              </select>
            </div>

            <div>
              <div><small>Chore</small></div>
              <select name="chore_id" required>
                {% for c in chores %}
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
        body=render_template_string(body, kids=kids, chores=chores, dollars=dollars),
    )


@app.post("/kid/log")
def kid_log():
    kid_name = request.form.get("kid_name", "").strip()
    chore_id = request.form.get("chore_id", "").strip()
    game.submit_chore(kid_name, chore_id)
    return redirect(url_for("kid"))


@app.get("/parent")
def parent():
    owed = game.owed_by_kid()
    done = game.chores_count_by_kid()
    pending = game.pending()

    kids = sorted(game.kids.keys())
    recent = list(enumerate(game.ledger))[-25:]  # last 25 with original indices
    recent.reverse()

    body = """
    <h1>Parent Dashboard</h1>

    <div class="card">
      <h2>Owed</h2>
      {% if not kids %}
        <p>No kids yet.</p>
      {% else %}
        <table>
          <tr><th>Kid</th><th>Chores logged</th><th>Owe (approved, unpaid)</th><th>Pay</th></tr>
          {% for k in kids %}
            <tr>
              <td>{{k}}</td>
              <td>{{ done.get(k, 0) }}</td>
              <td><b>{{ dollars(owed.get(k, 0)) }}</b></td>
              <td>
                <form method="post" action="{{ url_for('pay_kid') }}">
                  <input type="hidden" name="kid_name" value="{{k}}" />
                  <button class="btn" type="submit">Mark paid</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
        <p><small>“Owe” = sum of APPROVED chores not yet marked PAID.</small></p>
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
                  <input type="hidden" name="idx" value="{{ idx }}" />
                  <input type="hidden" name="approve" value="1" />
                  <button class="btn btn-primary" type="submit">Approve</button>
                </form>
                <form method="post" action="{{ url_for('approve') }}">
                  <input type="hidden" name="idx" value="{{ idx }}" />
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
      <h2>Add a kid</h2>
      <form method="post" action="{{ url_for('add_kid') }}">
        <div class="row">
          <input name="kid_name" placeholder="Kid name" required />
          <button class="btn btn-primary" type="submit">Add</button>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Recent activity</h2>
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
    """
    return render_template_string(
        BASE,
        title="Parent",
        body=render_template_string(
            body,
            kids=kids,
            owed=owed,
            done=done,
            pending=pending,
            recent=recent,
            dollars=dollars,
        ),
    )


@app.post("/parent/approve")
def approve():
    idx = int(request.form.get("idx", "-1"))
    approve_val = request.form.get("approve", "0") == "1"
    game.approve_or_deny(idx, approve_val)
    return redirect(url_for("parent"))


@app.post("/parent/pay")
def pay_kid():
    kid_name = request.form.get("kid_name", "").strip()
    game.mark_paid_for_kid(kid_name)
    return redirect(url_for("parent"))


@app.post("/parent/add_kid")
def add_kid():
    kid_name = request.form.get("kid_name", "").strip()
    game.add_kid(kid_name)
    return redirect(url_for("parent"))


@app.get("/parent/edit_chores")
def edit_chores():
    chores = sorted(game.chores.values(), key=lambda c: c.title.lower())

    body = """
    <h1>Edit Chores & Payouts</h1>

    <div class="card">
      <h2>Add / Update chore</h2>
      <form method="post" action="{{ url_for('save_chore') }}">
        <div class="row">
          <input name="id" placeholder="id (e.g. vacuum)" required />
          <input name="title" placeholder="title (what kids see)" required />
          <input name="reward_cents" placeholder="reward cents (e.g. 75)" required />
          <select name="requires_approval">
            <option value="1">Needs approval</option>
            <option value="0">Auto-approved</option>
          </select>
          <button class="btn btn-primary" type="submit">Save</button>
        </div>
        <p><small>Tip: use cents (100 = $1.00).</small></p>
      </form>
    </div>

    <div class="card">
      <h2>Current chores</h2>
      {% if not chores %}
        <p>No chores yet.</p>
      {% else %}
        <table>
          <tr><th>ID</th><th>Title</th><th>Payout</th><th>Approval</th><th>Delete</th></tr>
          {% for c in chores %}
            <tr>
              <td>{{ c.id }}</td>
              <td>{{ c.title }}</td>
              <td>{{ dollars(c.reward_cents) }}</td>
              <td>{% if c.requires_approval %}needs approval{% else %}auto{% endif %}</td>
              <td>
                <form method="post" action="{{ url_for('delete_chore') }}">
                  <input type="hidden" name="id" value="{{ c.id }}" />
                  <button class="btn btn-danger" type="submit">Delete</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
      {% endif %}
    </div>

    <div class="card">
      <a class="btn" href="{{ url_for('parent') }}">← Back to Parent</a>
    </div>
    """
    return render_template_string(
        BASE,
        title="Edit chores",
        body=render_template_string(body, chores=chores, dollars=dollars),
    )


@app.post("/parent/edit_chores/save")
def save_chore():
    cid = request.form.get("id", "").strip()
    title = request.form.get("title", "").strip()
    reward_raw = request.form.get("reward_cents", "").strip()
    requires_approval = request.form.get("requires_approval", "1") == "1"

    try:
        reward_cents = int(reward_raw)
    except ValueError:
        reward_cents = 0

    game.add_or_update_chore(Chore(cid, title, reward_cents, requires_approval))
    return redirect(url_for("edit_chores"))


@app.post("/parent/edit_chores/delete")
def delete_chore():
    cid = request.form.get("id", "").strip()
    game.delete_chore(cid)
    return redirect(url_for("edit_chores"))


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
