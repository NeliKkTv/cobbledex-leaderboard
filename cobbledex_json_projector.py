# cobbledex_json_projector.py — version SFTP ready
# Deps: pillow, flask, paramiko (déjà dans requirements.txt)

import os, json, time, re, posixpath
from pathlib import Path
from threading import Thread
from flask import Flask, send_file, make_response
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIG ENV ----------
FTP_MODE   = os.getenv("FTP_MODE", "ftp").lower()        # "ftp" ou "sftp"
FTP_HOST   = os.getenv("FTP_HOST", "")
FTP_USER   = os.getenv("FTP_USER", "")
FTP_PASS   = os.getenv("FTP_PASS", "")
FTP_PATH   = os.getenv("FTP_PATH", "/world/cobblemonplayerdata")
SFTP_PORT  = int(os.getenv("SFTP_PORT", "22"))           # change si Nitroserv utilise 2222
REFRESH_S  = int(os.getenv("REFRESH_SECONDS", "300"))

LOCAL_DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", "/tmp/cobblemon_data")).resolve()
LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH   = Path(os.getenv("OUT_PATH", "/tmp/leaderboard.png"))
TITLE      = os.getenv("TITLE", "Pokédex — Capturés")
MAX_ROWS   = int(os.getenv("MAX_ROWS", "30"))
PORT       = int(os.getenv("PORT", os.getenv("HTTP_PORT", "10000")))

try:
    FONT_TITLE = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
    FONT_ROW   = ImageFont.truetype("DejaVuSans.ttf", 36)
except:
    FONT_TITLE = ImageFont.load_default()
    FONT_ROW   = ImageFont.load_default()

last_rows = []

# ---------- DEBUG ENV ----------
print("[DEBUG] Variables d'environnement détectées:")
for key in ("FTP_MODE", "FTP_HOST", "FTP_USER", "FTP_PATH", "SFTP_PORT"):
    print(f"   {key} =", os.getenv(key))

# ---------- TRANSFERT FTP / SFTP ----------
def download_jsons(dest_dir: Path):
    """Télécharge tous les .json depuis FTP_PATH vers dest_dir (FTP ou SFTP)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0

    if FTP_MODE == "sftp":
        print(f"[SFTP] Connexion à {FTP_HOST}:{SFTP_PORT} ...")
        try:
            import paramiko
        except Exception as e:
            print("[SFTP] ERREUR: paramiko non installé:", e)
            return 0

        transport = None
        sftp = None
        try:
            transport = paramiko.Transport((FTP_HOST, SFTP_PORT))
            transport.connect(username=FTP_USER, password=FTP_PASS)
            sftp = paramiko.SFTPClient.from_transport(transport)

            # Normalise le chemin (pas de double slash)
            remote_dir = FTP_PATH.rstrip("/")
            print(f"[SFTP] Parcours du dossier: {remote_dir}")
            for entry in sftp.listdir_attr(remote_dir):
                if entry.filename.lower().endswith(".json"):
                    remote_file = posixpath.join(remote_dir, entry.filename)
                    local_path = dest_dir / entry.filename
                    sftp.get(remote_file, str(local_path))
                    downloaded += 1
            print(f"[SFTP] Téléchargés: {downloaded} fichiers JSON depuis {remote_dir}")
        except Exception as e:
            import traceback
            print("[SFTP] ERREUR:", e)
            traceback.print_exc()

        finally:
            try:
                if sftp: sftp.close()
                if transport: transport.close()
            except: pass
        return downloaded

    # ----- FTP classique (fallback) -----
    from ftplib import FTP, error_perm
    print(f"[FTP] Connexion à {FTP_HOST} ...")
    try:
        with FTP(FTP_HOST) as ftp:
            ftp.login(user=FTP_USER, passwd=FTP_PASS)
            ftp.set_pasv(True)
            remote_dir = FTP_PATH.rstrip("/")
            print(f"[FTP] Parcours du dossier: {remote_dir}")
            ftp.cwd(remote_dir)
            files = ftp.nlst()
            for fname in files:
                if not fname.lower().endswith(".json"):
                    continue
                local = dest_dir / fname
                with open(local, "wb") as f:
                    ftp.retrbinary(f"RETR {fname}", f.write)
                downloaded += 1
            print(f"[FTP] Téléchargés: {downloaded} fichiers JSON depuis {remote_dir}")
    except Exception as e:
        print("[FTP] ERREUR:", e)
    return downloaded

# ---------- PARSE DONNÉES ----------
def load_usercache_map():
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

def guess_name(usercache_map, fpath: Path, obj: dict):
    m = re.search(r"([0-9a-f]{32})", fpath.stem.lower())
    if m:
        uuid = m.group(1)
        if uuid in usercache_map:
            return usercache_map[uuid]
    for k in ("playerName","name","player"):
        v = obj.get(k)
        if isinstance(v,str) and 1 <= len(v) <= 32:
            return v
    return fpath.stem[:16]

def count_caught_species(obj):
    try:
        pokedex = obj.get("pokedex", {})
        if isinstance(pokedex, dict):
            if "caught" in pokedex and isinstance(pokedex["caught"], list):
                return len(set(map(str, pokedex["caught"])))
            if "caughtCount" in pokedex and isinstance(pokedex["caughtCount"], int):
                return pokedex["caughtCount"]
    except: pass
    for key in ("caughtSpecies","caught_species","capturedSpecies","captured"):
        if key in obj and isinstance(obj[key], list):
            return len(set(map(str, obj[key])))
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

def collect_rows():
    rows = []
    usercache_map = load_usercache_map()
    files = list(LOCAL_DATA_DIR.glob("*.json"))
    for f in files:
        try:
            data = json.load(open(f, "r", encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        name = guess_name(usercache_map, f, data)
        cnt = int(count_caught_species(data))
        rows.append((name, cnt))
    rows.sort(key=lambda t: (-t[1], t[0].lower()))
    return rows

# ---------- RENDU IMAGE ----------
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

# ---------- BOUCLE ----------
def updater_loop():
    global last_rows
    while True:
        if not (FTP_HOST and FTP_USER and FTP_PASS):
            print("[CFG] Manque FTP_HOST/FTP_USER/FTP_PASS — attente…")
        else:
            n = download_jsons(LOCAL_DATA_DIR)
            print(f"[SYNC] Fichiers ramenés: {n}")
        last_rows = collect_rows()
        print(f"[BUILD] Joueurs trouvés: {len(last_rows)}")
        make_image(last_rows)
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
    app.run(host="0.0.0.0", port=PORT, debug=False)
