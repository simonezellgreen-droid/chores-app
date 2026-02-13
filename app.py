from flask import Flask, request, redirect, session, render_template_string
import os, json
from datetime import date

app = Flask(__name__)
app.secret_key = "change_this_secret"

DATA_FILE = "data.json"
FAMILY_CODE = "FAMILY123"   # change this


# ---------------- DATA ----------------

def load():
    if not os.path.exists(DATA_FILE):
        return {
            "kids": [],
            "chores": {
                "bed": {"title": "Make Bed", "reward": 50, "approval": False},
                "dishes": {"title": "Dishes", "reward": 100, "approval": True}
            },
            "ledger": []
        }
    return json.load(open(DATA_FILE))


def save(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)


def dollars(c):
    return f"${c/100:.2f}"


data = load()


# ---------------- LOGIN ----------------

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["code"] == FAMILY_CODE:
            session["family"] = True
            return redirect("/menu")
    return """
    <h1>Family Login</h1>
    <form method="post">
      <input name="code" placeholder="Family Code">
      <button>Enter</button>
    </form>
    """


def require_login():
    if not session.get("family"):
        return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ---------------- MENU ----------------

@app.route("/menu")
def menu():
    if require_login(): return require_login()
    return """
    <h1>Main Menu</h1>
    <a href='/kid'>Kid Dashboard</a><br>
    <a href='/parent'>Parent Dashboard</a><br>
    <a href='/logout'>Logout</a>
    """


# ---------------- ADD KIDS ----------------

@app.route("/add_kid", methods=["POST"])
def add_kid():
    name = request.form["name"]
    if name and name not in data["kids"]:
        data["kids"].append(name)
        save(data)
    return redirect("/parent")


# ---------------- KID DASHBOARD ----------------

@app.route("/kid")
def kid():
    if require_login(): return require_login()

    template = """
    <h1>Kid Dashboard</h1>

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

    <h3>View Summary</h3>
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

    return render_template_string(template,
                                  kids=data["kids"],
                                  chores=data["chores"],
                                  dollars=dollars)


@app.route("/log", methods=["POST"])
def log():
    kid = request.form["kid"]
    chore_id = request.form["chore"]
    chore = data["chores"][chore_id]

    entry = {
        "kid": kid,
        "chore": chore["title"],
        "reward": chore["reward"],
        "status": "pending" if chore["approval"] else "approved",
        "date": str(date.today())
    }

    data["ledger"].append(entry)
    save(data)
    return redirect("/kid")


# ---------------- KID SUMMARY ----------------

@app.route("/kid_summary")
def kid_summary():
    kid = request.args["kid"]

    entries = [e for e in data["ledger"] if e["kid"] == kid]

    approved = sum(e["reward"] for e in entries if e["status"] == "approved")
    paid = sum(e["reward"] for e in entries if e["status"] == "paid")

    template = """
    <h1>{{kid}} Summary</h1>

    <p>Approved: {{dollars(approved)}}</p>
    <p>Paid: {{dollars(paid)}}</p>

    <ul>
    {% for e in entries %}
        <li>{{e.chore}} - {{e.status}}</li>
    {% endfor %}
    </ul>

    <a href='/kid'>Back</a>
    """

    return render_template_string(template,
                                  kid=kid,
                                  entries=entries,
                                  approved=approved,
                                  paid=paid,
                                  dollars=dollars)


# ---------------- PARENT DASHBOARD ----------------

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

    <h3>Add Kid</h3>
    <form method="post" action="/add_kid">
      <input name="name" placeholder="Kid name">
      <button>Add</button>
    </form>

    <h3>Money Owed</h3>
    <ul>
    {% for kid, amount in owed.items() %}
        <li>
          <b>{{kid}}</b> - {{dollars(amount)}}

          <form method="post" action="/pay" style="display:inline;">
            <input type="hidden" name="kid" value="{{kid}}">
            <button>Mark Paid</button>
          </form>

          <ul>
          {% for e in ledger %}
              {% if e.kid == kid and e.status == "approved" %}
                  <li>{{e.chore}} ({{dollars(e.reward)}})</li>
              {% endif %}
          {% endfor %}
          </ul>
        </li>
    {% endfor %}
    </ul>

    <h3>Pending Approvals</h3>
    {% for i, e in enumerate(ledger) %}
        {% if e.status == "pending" %}
            <div>
              {{e.kid}} - {{e.chore}}

              <form method="post" action="/approve" style="display:inline;">
                <input type="hidden" name="index" value="{{i}}">
                <button name="action" value="approve">Approve</button>
                <button name="action" value="deny">Deny</button>
              </form>
            </div>
        {% endif %}
    {% endfor %}

    <a href='/menu'>Back</a>
    """

    return render_template_string(template,
                                  owed=owed,
                                  ledger=data["ledger"],
                                  enumerate=enumerate,
                                  dollars=dollars)


@app.route("/approve", methods=["POST"])
def approve():
    index = int(request.form["index"])
    action = request.form["action"]

    if action == "approve":
        data["ledger"][index]["status"] = "approved"
    else:
        data["ledger"][index]["status"] = "denied"

    save(data)
    return redirect("/parent")


@app.route("/pay", methods=["POST"])
def pay():
    kid = request.form["kid"]

    for e in data["ledger"]:
        if e["kid"] == kid and e["status"] == "approved":
            e["status"] = "paid"

    save(data)
    return redirect("/parent")


# ---------------- RUN ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
