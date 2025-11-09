import os
from flask import Flask, request, send_from_directory, abort
from werkzeug.utils import secure_filename

API_TOKEN = os.getenv("API_TOKEN", "")  # token de sécurité pour les uploads

app = Flask(__name__)

BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

OUT_FILE = os.path.join(STATIC_DIR, "leaderboard.png")

@app.route("/")
def home():
    return '<h3>CobbleDex Leaderboard</h3><p><a href="/leaderboard.png">Voir le leaderboard</a></p>'

@app.route("/leaderboard.png")
def leaderboard_png():
    if not os.path.exists(OUT_FILE):
        abort(404)
    return send_from_directory(STATIC_DIR, "leaderboard.png", mimetype="image/png", max_age=0)

@app.route("/upload", methods=["POST"])
def upload():
    if request.headers.get("X-API-KEY") != API_TOKEN:
        abort(401)
    f = request.files.get("file")
    if not f:
        abort(400)
    filename = secure_filename("leaderboard.png")
    f.save(os.path.join(STATIC_DIR, filename))
    return {"ok": True}
