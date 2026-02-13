from flask import Flask, request, redirect, session, render_template_string
import os
import json
import time
from datetime import datetime, date

app = Flask(__name__)
app.secret_key = "super_secret_key_change_me"

DATA_FILE = "data.json"
FAMILY_CODE = "FAMILY123"  # change this


# --------------------------
# Data Helpers
# --------------------------

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "kids": {},
            "chores": {
                "make_bed": {"title": "Make Bed", "reward": 50, "approval": False},
                "dishes": {"title": "Dishes", "reward": 100, "approval": True}
            },
            "ledger": [],
            "goals": {}
        }
    return json.load(open(DATA_FILE))


def save_data(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)


def dollars(cents):
    return f"${cents/100:.2f}"


data = load_data()


# --------------------------
# Family Login
# --------------------------

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        if request.form["code"] == FAMILY_CODE:
            session["family"] = True
            return redirect("/menu")
    return """
    <h1>Family Login</h1>
    <form method="post">
        <input name="code" placeholder="Enter family code">
        <button>Enter</button>
    </form>
    """


def require_login():
    if not session.get("family"):
        return redirect("/")
    return None


# --------------------------
# Main Menu
# --------------------------

@app.route("/menu")
def menu():
    if require_login(): return require_login()
    return """
    <h1>Main Menu</h1>
    <a href='/kid'>Kid Menu</a><br>
    <a href='/parent'>Parent Dashboard</a><br>
    <a href='/logout'>Logout</a>
    """


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# --------------------------
# Kid Menu
# --------------------------

@app.route("/kid")
def kid():
    if require_login(): return require_login()

    kids = data["kids"]
    chores = data["chores"]

    template = """
    <h1>Kid Menu</h1>

    <form method="post" action="/log">
        <select name="kid">
            {% for k in kids %}
                <option>{{k}}</option>
            {% endfor %}
        </select>

        <select name="chore">
            {% for id, c in chores.items() %}
                <option value="{{id}}">
                    {{c.title}} ({{dollars(c.reward)}})
                </option>
            {% endfor %}
        </select>

        <button>Log Chore</button>
    </form>

    <h2>Kid Summary</h2>
    <form method="get" action="/kid_summary">
        <select name="kid">
            {% for k in kids %}
                <option>{{k}}</option>
            {% endfor %}
        </select>
        <button>View</button>
    </form>

    <a href='/menu'>Back</a>
    """

    return render_template_string(template, kids=kids, chores=chores, dollars=dollars)


@app.route("/log", methods=["POST"])
def log():
    kid = request.form["kid"]
    chore_id = request.form["chore"]

    chore = data["chores"][chore_id]

    entry = {
        "kid": kid,
        "chore": chore["title"],
        "reward": chore["reward"],
        "approval": chore["approval"],
        "status": "pending" if chore["approval"] else "approved",
        "date": str(date.today())
    }

    data["ledger"].append(entry)
    save_data(data)
    return redirect("/kid")


# --------------------------
# Kid Summary
# --------------------------

@app.route("/kid_summary")
def kid_summary():
    kid = request.args["kid"]

    entries = [e for e in data["ledger"] if e["kid"] == kid]
    total = sum(e["reward"] for e in entries if e["status"] == "approved")

    streak = calculate_streak(kid)

    template = """
    <h1>{{kid}} Summary</h1>

    <p>Total Approved Money: {{dollars(total)}}</p>
    <p>Current Streak: {{streak}} days</p>

    <h3>Chores Done</h3>
    <ul>
    {% for e in entries %}
        <li>{{e.chore}} - {{e.status}}</li>
    {% endfor %}
    </ul>

    <a href='/kid'>Back</a>
    """

    return render_template_string(template, kid=kid, entries=entries,
                                  total=total, dollars=dollars, streak=streak)


def calculate_streak(kid):
    dates = sorted({e["date"] for e in data["ledger"] if e["kid"] == kid})
    if not dates: return 0

    streak = 0
    today = date.today()

    for i in range(len(dates)):
        check = today.replace(day=today.day - i)
        if str(check) in dates:
            streak += 1
        else:
            break
    return streak


# --------------------------
# Parent Dashboard
# --------------------------

@app.route("/parent")
def parent():
    if require_login(): return require_login()

    owed = {}
    for kid in data["kids"]:
        owed[kid] = sum(
            e["reward"] for e in data["ledger"]
            if e["kid"] == kid and e["status"] == "approved"
        )

    template = """
    <h1>Parent Dashboard</h1>

    <h2>Money Owed</h2>
    <ul>
    {% for kid, amount in owed.items() %}
        <li>{{kid}} - {{dollars(amount)}}
            <form method="post" action="/pay" style="display:inline;">
                <input type="hidden" name="kid" value="{{kid}}">
                <button>Mark Paid</button>
            </form>
        </li>
    {% endfor %}
    </ul>

    <h2>Pending Approvals</h2>
    <ul>
    {% for i, e in enumerate(data["ledger"]) %}
        {% if e.status == "pending" %}
            <li>
                {{e.kid}} - {{e.chore}}
                <form method="post" action="/approve" style="display:inline;">
                    <input type="hidden" name="i" value="{{i}}">
                    <button name="action" value="approve">Approve</button>
                    <button name="action" value="deny">Deny</button>
                </form>
            </li>
        {% endif %}
    {% endfor %}
    </ul>

    <a href='/edit_chores'>Edit Chores</a><br>
    <a href='/menu'>Back</a>
    """

    return render_template_string(template, owed=owed, dollars=dollars,
                                  data=data, enumerate=enumerate)


@app.route("/approve", methods=["POST"])
def approve():
    i = int(request.form["i"])
    action = request.form["action"]

    if action == "approve":
        data["ledger"][i]["status"] = "approved"
    else:
        data["ledger"][i]["status"] = "denied"

    save_data(data)
    return redirect("/parent")


@app.route("/pay", methods=["POST"])
def pay():
    kid = request.form["kid"]

    for e in data["ledger"]:
        if e["kid"] == kid and e["status"] == "approved":
            e["status"] = "paid"

    save_data(data)
    return redirect("/parent")


# --------------------------
# Edit Chores
# --------------------------

@app.route("/edit_chores", methods=["GET", "POST"])
def edit_chores():
    if require_login(): return require_login()

    if request.method == "POST":
        data["chores"][request.form["id"]] = {
            "title": request.form["title"],
            "reward": int(request.form["reward"]),
            "approval": request.form.get("approval") == "on"
        }
        save_data(data)
        return redirect("/edit_chores")

    template = """
    <h1>Edit Chores</h1>

    <form method="post">
        <input name="id" placeholder="id">
        <input name="title" placeholder="title">
        <input name="reward" placeholder="reward cents">
        Needs approval <input type="checkbox" name="approval">
        <button>Save</button>
    </form>

    <h2>Current Chores</h2>
    <ul>
    {% for id, c in data["chores"].items() %}
        <li>{{c.title}} - {{dollars(c.reward)}}</li>
    {% endfor %}
    </ul>

    <a href='/parent'>Back</a>
    """

    return render_template_string(template, data=data, dollars=dollars)


# --------------------------
# Run
# --------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
