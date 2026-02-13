from __future__ import annotations

import os, json, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List
from functools import wraps

from flask import (
    Flask, request, redirect, url_for,
    render_template_string, session
)

# ============================================================
# App + Security
# ============================================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

FAMILY_CODE = os.environ.get("FAMILY_CODE", "1234")
DATA_FILE = "chores_game.json"


def dollars(cents: int) -> str:
    return f"${cents/100:.2f}"


def require_family(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("family_ok"):
            return view(*args, **kwargs)

        if request.method == "POST":
            if request.form.get("code") == FAMILY_CODE:
                session["family_ok"] = True
                return redirect(request.path)

        return render_template_string(
            BASE,
            title="Family Code",
            body="""
            <h1>Family Code</h1>
            <div class="card">
              <form method="post">
                <input name="code" placeholder="Family code" required />
                <button class="btn btn-primary">Enter</button>
              </form>
            </div>
            """
        )
    return wrapped


# ============================================================
# Data Models
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
    kid: str
    chore: str
    reward_cents: int
    ts: float
    status: str  # pending | approved | denied | paid


# ============================================================
# Storage
# ============================================================

class Store:
    def __init__(self):
        self.path = Path(DATA_FILE)

    def load(self):
        if not self.path.exists():
            return {"kids": {}, "chores": {}, "ledger": []}
        return json.loads(self.path.read_text())

    def save(self, data):
        self.path.write_text(json.dumps(data, indent=2))


store = Store()
data = store.load()


# ============================================================
# Game Logic
# ============================================================

kids: Dict[str, Kid] = {
    k: Kid(**v) for k, v in data["kids"].items()
}

chores: Dict[str, Chore] = {
    c: Chore(**v) for c, v in data["chores"].items()
}

ledger: List[LedgerEntry] = [
    LedgerEntry(**e) for e in data["ledger"]
]

if not chores:
    chores["bed"] = Chore("bed", "Make bed", 50, False)
    chores["dishes"] = Chore("dishes", "Unload dishwasher", 100, True)


def save_all():
    store.save({
        "kids": {k: asdict(v) for k, v in kids.items()},
        "chores": {k: asdict(v) for k, v in chores.items()},
        "ledger": [asdict(e) for e in ledger]
    })


# ============================================================
# UI Base
# ============================================================

BASE = """
<!doctype html>
<title>{{title}}</title>
<style>
body { font-family: Arial; max-width: 900px; margin: 20px auto; }
.card { border: 1px solid #ddd; padding: 14px; margin: 14px 0; border-radius: 12px; }
.btn { padding: 8px 12px; border-radius: 10px; border: 1px solid #aaa; background: #eee; }
.btn-primary { background: black; color: white; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 6px; border-bottom: 1px solid #eee; }
</style>

<nav>
<a href="/">Home</a> |
<a href="/kid">Kid</a> |
<a href="/parent">Parent</a> |
<a href="/logout">Logout</a>
</nav>
<hr>

{{ body|safe }}
"""


# ============================================================
# Routes
# ============================================================

@app.route("/", methods=["GET", "POST"])
@require_family
def home():
    return render_template_string(
        BASE,
        title="Home",
        body="""
        <h1>Chores App</h1>
        <div class="card">
          <a class="btn btn-primary" href="/kid">Kid Menu</a>
          <a class="btn btn-primary" href="/parent">Parent Menu</a>
        </div>
        """
    )


@app.get("/kid")
@require_family
def kid_menu():
    return render_template_string(
        BASE,
        title="Kid",
        body="""
        <h1>Kid Menu</h1>
        <div class="card">
          <form method="post" action="/kid/log">
            <select name="kid">
              {% for k in kids %}<option>{{k}}</option>{% endfor %}
            </select>
            <select name="chore">
              {% for c in chores.values() %}
                <option value="{{c.id}}">
                  {{c.title}} ({{ dollars(c.reward_cents) }})
                </option>
              {% endfor %}
            </select>
            <button class="btn btn-primary">Log Chore</button>
          </form>
        </div>

        <div class="card">
          <h2>Kid Summary</h2>
          <form method="get" action="/kid/summary">
            <select name="kid">
              {% for k in kids %}<option>{{k}}</option>{% endfor %}
            </select>
            <button class="btn">View</button>
          </form>
        </div>
        """,
        kids=kids.keys(),
        chores=chores,
        dollars=dollars
    )


@app.post("/kid/log")
@require_family
def kid_log():
    kid = request.form["kid"]
    chore = chores[request.form["chore"]]
    status = "pending" if chore.requires_approval else "approved"
    ledger.append(LedgerEntry(kid, chore.title, chore.reward_cents, time.time(), status))
    save_all()
    return redirect("/kid")


@app.get("/kid/summary")
@require_family
def kid_summary():
    kid = request.args["kid"]
    entries = [e for e in ledger if e.kid == kid]
    approved = sum(e.reward_cents for e in entries if e.status == "approved")
    paid = sum(e.reward_cents for e in entries if e.status == "paid")

    return render_template_string(
        BASE,
        title="Kid Summary",
        body="""
        <h1>{{kid}}'s Summary</h1>
        <p>Approved: {{ dollars(approved) }}</p>
        <p>Paid: {{ dollars(paid) }}</p>

        <ul>
        {% for e in entries %}
          <li>{{ e.chore }} – {{ e.status }}</li>
        {% endfor %}
        </ul>
        """,
        kid=kid,
        entries=entries,
        approved=approved,
        paid=paid,
        dollars=dollars
    )


@app.get("/parent")
@require_family
def parent():
    owed = {}
    for k in kids:
        owed[k] = sum(
            e.reward_cents for e in ledger
            if e.kid == k and e.status == "approved"
        )

    return render_template_string(
        BASE,
        title="Parent",
        body="""
        <h1>Parent Dashboard</h1>
        <table>
          <tr><th>Kid</th><th>Owed</th><th>Pay</th></tr>
          {% for k, amt in owed.items() %}
            <tr>
              <td>{{k}}</td>
              <td>{{ dollars(amt) }}</td>
              <td>
                <form method="post" action="/parent/pay">
                  <input type="hidden" name="kid" value="{{k}}">
                  <button class="btn">Mark Paid</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </table>
        """,
        owed=owed,
        dollars=dollars
    )


@app.post("/parent/pay")
@require_family
def parent_pay():
    kid = request.form["kid"]
    for e in ledger:
        if e.kid == kid and e.status == "approved":
            e.status = "paid"
    save_all()
    return redirect("/parent")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")
