# cobbledex_json_projector.py
# Dépendances: pillow, flask (ftplib est dans la stdlib)
#
# FONCTIONNEMENT
# - Toutes les REFRESH_SECONDS (par défaut 300 s), le script:
#   1) se connecte en FTP à Nitroserv
#   2) télécharge les fichiers JSON Cobblemon depuis FTP_PATH vers un dossier local
#   3) lit/compte le nombre d'espèces capturées par joueur
#   4) génère leaderboard.png
# - L'image est servie sur http://<host>:<PORT>/leaderboard.png

import os, json, time, re
from pathlib import Path
from threading import Thread
from ftplib import FTP, error_perm

from flask import Flask, send_file, make_response
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIG PAR VARIABLES D'ENV ----------
FTP_HOST    = os.getenv("FTP_HOST", "")
FTP_USER    = os.getenv("FTP_USER", "")
FTP_PASS    = os.getenv("FTP_PASS", "")
FTP_PATH    = os.getenv("FTP_PATH", "/world/cobblemonplayerdata/")  # chemin dossier JSON côté Nitroserv
FTP_PASSIVE = os.getenv("FTP_PASSIVE", "true").lower() != "false"

# Dossier local (en lecture/écriture). Sur Render, /tmp est garanti en écriture.
LOCAL_DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", "/tmp/cobblemon_data")).resolve()
LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)

OUT_PATH   = Path(os.getenv("OUT_PATH", "/tmp/leaderboard.png")).resolve()
TITLE      = os.getenv("TITLE", "Pokédex — Capturés")
MAX_ROWS   = int(os.getenv("MAX_ROWS", "30"))
REFRESH_S  = int(os.getenv("REFRESH_SECONDS", "300"))  # 5 min

# Render donne PORT, sinon fallback
PORT = int(os.getenv("PORT", os.getenv("HTTP_PORT", "10000")))

# Polices
try:
    FONT_TITLE = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
    FONT_ROW   = ImageFont.truetype("DejaVuSans.ttf", 36)
except:
    FONT_TITLE = ImageFont.load_default()
    FONT_ROW   = ImageFont.load_default()

last_rows = []  # [(name, count)]

# ---------- FTP ----------
def ftp_list_json_files(ftp, path):
    try:
        ftp.cwd(path)
    except error_perm as e:
        print("[FTP] Impossible d'entrer dans", path, e)
        return []
    try:
        names = ftp.nlst()
    except Exception as e:
        print("[FTP] nlst échoué:", e)
        return []
    return [n for n in names if n.lower().endswith(".json")]

def ftp_download_folder(host, user, password, path, passive=True, dest_dir=LOCAL_DATA_DIR):
    dest_dir.mkdir(parents=True, exist_ok=True)
    with FTP(host) as ftp:
        ftp.login(user=user, passwd=password)
        ftp.set_pasv(passive)
        files = ftp_list_json_files(ftp, path)
        downloaded = 0
        for fname in files:
            local = dest_dir / fname
            try:
                with open(local, "wb") as f:
                    ftp.retrbinary(f"RETR {fname}", f.write)
                downloaded += 1
            except Exception as e:
                print(f"[FTP] Echec RETR {fname}:", e)
        print(f"[FTP] Téléchargés: {downloaded} fichiers JSON depuis {path}")

# ---------- PARSE ----------
def load_usercache_map(usercache_file: Path):
    # Si Nitroserv expose usercache.json à la racine FTP, on peut le déposer nous-mêmes dans dest_dir
    # Ici on regarde s'il a déjà été rapatrié dans LOCAL_DATA_DIR (optionnel)
    m = {}
    f = LOCAL_DATA_DIR / "usercache.json"
    if not f.exists():
        return m
    try:
        data = json.load(open(f, "r", encoding="utf-8"))
        for e in data:
            uuid = re.sub(r"[-]", "", e.get("uuid","")).lower()
            name = e.get("name","")
            if uuid and name:
                m[uuid] = name
    except Exception:
        pass
    return m

def guess_name_from_file(usercache_map, fpath: Path, obj: dict):
    # 1) UUID dans le nom du fichier
    s = fpath.stem.lower()
    m = re.search(r"([0-9a-f]{32})", s)
    if m:
        uuid = m.group(1)
        if uuid in usercache_map:
            return usercache_map[uuid]
    # 2) tenter champs internes
    for k in ("playerName","name","player"):
        v = obj.get(k)
        if isinstance(v,str) and 1 <= len(v) <= 32:
            return v
    # 3) fallback = début de nom de fichier
    return fpath.stem[:16]

def count_caught_species(obj):
    # Cherche des structures fréquentes
    try:
        pokedex = obj.get("pokedex", {})
        if isinstance(pokedex, dict):
            if "caught" in pokedex and isinstance(pokedex["caught"], list):
                return len(set(map(str, pokedex["caught"])))
            if "caughtCount" in pokedex and isinstance(pokedex["caughtCount"], int):
                return pokedex["caughtCount"]
    except Exception:
        pass
    for key in ("caughtSpecies","caught_species","capturedSpecies","captured"):
        if key in obj and isinstance(obj[key], list):
            return len(set(map(str, obj[key])))

    # Recherche large
    def walk(o):
        best = 0
        if isinstance(o, dict):
            for k,v in o.items():
                if k.lower() in ("caught","caughtspecies","captured","capturedspecies") and isinstance(v, list):
                    best = max(best, len(set(map(str, v))))
                best = max(best, walk(v))
        elif isinstance(o, list):
            for it in o:
                best = max(best, walk(it))
        return best
    return walk(obj)

def collect_rows_from_local():
    rows = []
    usercache_map = load_usercache_map(LOCAL_DATA_DIR / "usercache.json")
    for f in LOCAL_DATA_DIR.glob("*.json"):
        try:
            data = json.load(open(f, "r", encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        name = guess_name_from_file(usercache_map, f, data)
        count = int(count_caught_species(data))
        rows.append((name, count))
    rows.sort(key=lambda t: (-t[1], t[0].lower()))
    return rows

# ---------- RENDER IMAGE ----------
def make_image(rows):
    W, H = 900, 80 + 60*max(1, len(rows[:MAX_ROWS]))
    img = Image.new("RGBA", (W, H), (18,18,22,255))
    d = ImageDraw.Draw(img)

    d.text((24, 18), TITLE, font=FONT_TITLE, fill=(255,255,255,255))
    y0 = 80
    d.text((24, y0), "Joueur", font=FONT_ROW, fill=(200,200,200,255))
    d.text((W-220, y0), "Pokédex", font=FONT_ROW, fill=(200,200,200,255))

    y = y0 + 42
    rank = 1
    for name, score in rows[:MAX_ROWS]:
        if rank % 2 == 0:
            d.rectangle([(16, y-6), (W-16, y+38)], fill=(32,32,38,255))
        d.text((24, y), f"{rank:>2}. {name}", font=FONT_ROW, fill=(255,255,255,255))
        sw = d.textlength(str(score), font=FONT_ROW)
        d.text((W-40 - sw, y), str(score), font=FONT_ROW, fill=(255,255,255,255))
        y += 48
        rank += 1

    img.save(OUT_PATH)

# ---------- UPDATE LOOP ----------
def updater_loop():
    global last_rows
    while True:
        try:
            if not (FTP_HOST and FTP_USER and FTP_PASS):
                print("[CFG] Manque FTP_HOST/FTP_USER/FTP_PASS — je réessaie plus tard.")
            else:
                ftp_download_folder(FTP_HOST, FTP_USER, FTP_PASS, FTP_PATH, passive=FTP_PASSIVE, dest_dir=LOCAL_DATA_DIR)
            last_rows = collect_rows_from_local()
            make_image(last_rows)
        except Exception as e:
            print("[ERR]", e)
        time.sleep(REFRESH_S)

# ---------- HTTP ----------
app = Flask(__name__)

@app.route("/leaderboard.png")
def leaderboard_png():
    resp = make_response(send_file(str(OUT_PATH), mimetype="image/png"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/debug")
def debug():
    return {"rows": last_rows, "count": len(last_rows)}

if __name__ == "__main__":
    Thread(target=updater_loop, daemon=True).start()
    # Render exige host=0.0.0.0 et port=$PORT
    app.run(host="0.0.0.0", port=PORT, debug=False)

