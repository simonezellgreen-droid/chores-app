from __future__ import annotations
import os, json, time
from dataclasses import dataclass, asdict
from pathlib import Path
from functools import wraps
from typing import Dict, List
from flask import Flask, request, redirect, url_for, render_template_string, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

FAMILY_CODE = os.environ.get("FAMILY_CODE", "1234")
DATA_FILE = "chores_game.json"


# -------------------- Utilities --------------------

def dollars(c):
    return f"${c/100:.2f}"


def require_family(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("family_ok"):
            return view(*args, **kwargs)

        if request.method == "POST":
            if request.form.get("code") == FAMILY_CODE:
                session["family_ok"] = True
                return redirect(request.path)

        return render_template_string(TEMPLATE, page="code")

    return wrapped


# -------------------- Models --------------------

@dataclass
class Kid:
    name: str
    goal: int = 0


@dataclass
class Chore:
    id: str
    title: str
    reward: int
    approval: bool = True


@dataclass
class Entry:
    kid: str
    chore: str
    reward: int
    ts: float
    status: str


# -------------------- Storage --------------------

def load():
    if not Path(DATA_FILE).exists():
        return {"kids": {}, "chores": {}, "ledger": []}
    return json.loads(Path(DATA_FILE).read_text())


def save(data):
    Path(DATA_FILE).write_text(json.dumps(data, indent=2))


data = load()
kids: Dict[str, Kid] = {k: Kid(**v) for k, v in data["kids"].items()}
chores: Dict[str, Chore] = {c: Chore(**v) for c, v in data["chores"].items()}
ledger: List[Entry] = [Entry(**e) for e in data["ledger"]]

if not chores:
    chores["bed"] = Chore("bed", "Make bed", 50, False)
    chores["dishes"] = Chore("dishes", "Unload dishwasher", 100, True)


def save_all():
    save({
        "kids": {k: asdict(v) for k, v in kids.items()},
        "chores": {k: asdict(v) for k, v in chores.items()},
        "ledger": [asdict(e) for e in ledger]
    })


# -------------------- Template --------------------

TEMPLATE = """
<!doctype html>
<title>Chores</title>
<style>
body { font-family: Arial; max-width: 900px; margin: 20px auto; }
.card { border: 1px solid #ccc; padding: 12px; margin: 12px 0; border-radius: 10px; }
button { padding: 6px 10px; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 6px; border-bottom: 1px solid #eee; }
nav a { margin-right: 10px; }
</style>

<nav>
<a href="/">Home</a>
<a href="/kid">Kid</a>
<a href="/parent">Parent</a>
<a href="/logout">Logout</a>
</nav>
<hr>

{% if page == "code" %}
<h2>Enter Family Code</h2>
<form method="post">
  <input name="code" required>
  <button>Enter</button>
</form>

{% elif page == "home" %}
<h1>Main Menu</h1>

{% elif page == "kid" %}
<h1>Kid Menu</h1>
<form method="post" action="/kid/log">
<select name="kid">
{% for k in kids %}
<option>{{k}}</option>
{% endfor %}
</select>

<select name="chore">
{% for c in chores %}
<option value="{{c.id}}">
{{c.title}} ({{ dollars(c.reward) }})
</option>
{% endfor %}
</select>
<button>Log</button>
</form>

<h3>View Summary</h3>
<form method="get" action="/kid/summary">
<select name="kid">
{% for k in kids %}
<option>{{k}}</option>
{% endfor %}
</select>
<button>View</button>
</form>

{% elif page == "kid_summary" %}
<h2>{{kid}} Summary</h2>
<p>Approved: {{ dollars(approved) }}</p>
<p>Paid: {{ dollars(paid) }}</p>
<p>Streak: {{streak}} chores</p>

<ul>
{% for e in entries %}
<li>{{e.chore}} - {{e.status}}</li>
{% endfor %}
</ul>

{% elif page == "parent" %}
<h1>Parent Dashboard</h1>

<table>
<tr><th>Kid</th><th>Owed</th><th>Details</th><th>Pay</th></tr>
{% for k in kids %}
<tr>
<td>{{k}}</td>
<td>{{ dollars(owed[k]) }}</td>
<td>
<ul>
{% for e in ledger if e.kid==k and e.status=="approved" %}
<li>{{e.chore}} ({{dollars(e.reward)}})</li>
{% endfor %}
</ul>
</td>
<td>
<form method="post" action="/parent/pay">
<input type="hidden" name="kid" value="{{k}}">
<button>Pay</button>
</form>
</td>
</tr>
{% endfor %}
</table>

<h3>Pending</h3>
{% for e in ledger if e.status=="pending" %}
<div class="card">
{{e.kid}} - {{e.chore}}
<form method="post" action="/parent/approve">
<input type="hidden" name="index" value="{{loop.index0}}">
<button name="action" value="approve">Approve</button>
<button name="action" value="deny">Deny</button>
</form>
</div>
{% endfor %}

{% endif %}
"""


# -------------------- Routes --------------------

@app.route("/", methods=["GET","POST"])
@require_family
def home():
    return render_template_string(TEMPLATE, page="home")


@app.get("/kid")
@require_family
def kid():
    return render_template_string(TEMPLATE, page="kid",
                                  kids=kids.keys(),
                                  chores=chores.values(),
                                  dollars=dollars)


@app.post("/kid/log")
@require_family
def kid_log():
    k = request.form["kid"]
    c = chores[request.form["chore"]]
    status = "pending" if c.approval else "approved"
    ledger.append(Entry(k, c.title, c.reward, time.time(), status))
    save_all()
    return redirect("/kid")


@app.get("/kid/summary")
@require_family
def kid_summary():
    kid = request.args["kid"]
    entries = [e for e in ledger if e.kid==kid]
    approved = sum(e.reward for e in entries if e.status=="approved")
    paid = sum(e.reward for e in entries if e.status=="paid")
    streak = len([e for e in entries if e.status!="denied"])
    return render_template_string(TEMPLATE, page="kid_summary",
                                  kid=kid, entries=entries,
                                  approved=approved, paid=paid,
                                  streak=streak, dollars=dollars)


@app.get("/parent")
@require_family
def parent():
    owed = {}
    for k in kids:
        owed[k] = sum(e.reward for e in ledger if e.kid==k and e.status=="approved")
    return render_template_string(TEMPLATE,
                                  page="parent",
                                  kids=kids.keys(),
                                  ledger=ledger,
                                  owed=owed,
                                  dollars=dollars)


@app.post("/parent/approve")
@require_family
def approve():
    index = int(request.form["index"])
    action = request.form["action"]
    if action=="approve":
        ledger[index].status="approved"
    else:
        ledger[index].status="denied"
    save_all()
    return redirect("/parent")


@app.post("/parent/pay")
@require_family
def pay():
    kid = request.form["kid"]
    for e in ledger:
        if e.kid==kid and e.status=="approved":
            e.status="paid"
    save_all()
    return redirect("/parent")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
