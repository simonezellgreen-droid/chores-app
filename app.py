from flask import Flask, request, redirect
import json
from pathlib import Path
import time
import os

app = Flask(__name__)
DB = Path("chores_game.json")


def load():
    if not DB.exists():
        return {
            "kids": ["Kid1", "Kid2"],
            "chores": {
                "make_bed": {"title": "Make bed", "cents": 50},
                "dishes": {"title": "Unload dishwasher", "cents": 100},
            },
            "ledger": []
        }
    return json.loads(DB.read_text())


def save(data):
    DB.write_text(json.dumps(data, indent=2))


@app.get("/")
def home():
    data = load()
    kids = data["kids"]
    chores = data["chores"]

    chore_options = "".join(
        f"<option value='{cid}'>{c['title']} (${c['cents']/100:.2f})</option>"
        for cid, c in chores.items()
    )
    kid_options = "".join(f"<option value='{k}'>{k}</option>" for k in kids)

    return f"""
    <h1>Chores</h1>
    <h2>Kid: log a chore</h2>
    <form method="post" action="/log">
      <label>Kid:</label>
      <select name="kid">{kid_options}</select>
      <label>Chore:</label>
      <select name="chore">{chore_options}</select>
      <button type="submit">Log</button>
    </form>

    <hr/>
    <h2>Parent</h2>
    <p><a href="/parent">Parent dashboard</a></p>
    """


@app.post("/log")
def log():
    data = load()
    kid = request.form["kid"]
    chore_id = request.form["chore"]
    chore = data["chores"][chore_id]

    data["ledger"].append({
        "kid": kid,
        "title": chore["title"],
        "cents": chore["cents"],
        "status": "approved",
        "ts": time.time()
    })
    save(data)
    return redirect("/")


@app.get("/parent")
def parent():
    data = load()
    owed = {k: 0 for k in data["kids"]}
    for e in data["ledger"]:
        if e["status"] == "approved":
            owed[e["kid"]] += e["cents"]

    owed_lines = "".join(f"<li>{k}: ${owed[k]/100:.2f}</li>" for k in data["kids"])

    return f"""
    <h1>Parent dashboard</h1>
    <p><a href="/">← back</a></p>
    <h2>Owed</h2>
    <ul>{owed_lines}</ul>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
