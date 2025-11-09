# cobbledex_json_projector.py — Clean SFTP/FTP edition (fix)
# Deps: pillow, flask, paramiko

import os, json, time, re, posixpath
from pathlib import Path
from threading import Thread
from typing import List, Tuple
from flask import Flask, send_file, make_response
from PIL import Image, ImageDraw, ImageFont

# ======= ENV =======
FTP_MODE   = os.getenv("FTP_MODE", "ftp").lower()          # "sftp" | "ftp"
FTP_HOST   = os.getenv("FTP_HOST", "")
FTP_USER   = os.getenv("FTP_USER", "")
FTP_PASS   = os.getenv("FTP_PASS", "")
FTP_PATH   = os.getenv("FTP_PATH", "/world/cobblemonplayerdata").rstrip("/")
SFTP_PORT  = int(os.getenv("SFTP_PORT", "22"))             # souvent 2222 chez Nitroserv
REFRESH_S  = int(os.getenv("REFRESH_SECONDS", "300"))

LOCAL_DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", "/tmp/cobblemon_data")).resolve()
LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH   = Path(os.getenv("OUT_PATH", "/tmp/leaderboard.png")).resolve()
TITLE      = os.getenv("TITLE", "Pokédex — Capturés")
MAX_ROWS   = int(os.getenv("MAX_ROWS", "30"))
PORT       = int(os.getenv("PORT", os.getenv("HTTP_PORT", "10000")))

# ======= FONTS =======
try:
    FONT_TITLE = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
    FONT_ROW   = ImageFont.truetype("DejaVuSans.ttf", 36)
except Exception:
    FONT_TITLE = ImageFont.load_default()
    FONT_ROW   = ImageFont.load_default()

last_rows: List[Tuple[str, int]] = []

# ======= LOG ENV =======
def log_env():
    print("[ENV] FTP_MODE  =", FTP_MODE)
    print("[ENV] FTP_HOST  =", FTP_HOST)
    print("[ENV] FTP_USER  =", FTP_USER)
    print("[ENV] FTP_PATH  =", FTP_PATH)
    print("[ENV] SFTP_PORT =", SFTP_PORT)
log_env()

# ======= TRANSFERTS =======
def sftp_list_and_download(remote_dir: str, dest_dir: Path) -> int:
    import paramiko, stat
    print(f"[SFTP] Connexion à {FTP_HOST}:{SFTP_PORT} ...")
    downloaded = 0
    transport = None
    sftp = None
    try:
        transport = paramiko.Transport((FTP_HOST, SFTP_PORT))
        transport.connect(username=FTP_USER, password=FTP_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)

        print(f"[SFTP] Parcours: {remote_dir}")
        entries = sftp.listdir_attr(remote_dir)
        names = [e.filename for e in entries]
        print(f"[SFTP] Entrées: {len(entries)} — {names[:30]}")

        for e in entries:
            if e.filename.lower().endswith(".json"):
                remote_file = posixpath.join(remote_dir, e.filename)
                local_path = dest_dir / e.filename
                sftp.get(remote_file, str(local_path))
                downloaded += 1

        # tente 1 sous-dossier si rien trouvé
        if downloaded == 0:
            for e in entries:
                if stat.S_ISDIR(e.st_mode):
                    sub = posixpath.join(remote_dir, e.filename)
                    try:
                        sub_entries = sftp.listdir_attr(sub)
                    except Exception as sub_err:
                        print(f"[SFTP] Impossible d’ouvrir {sub}: {sub_err}")
                        continue
                    for se in sub_entries:
                        if se.filename.lower().endswith(".json"):
                            remote_file = posixpath.join(sub, se.filename)
                            local_path = dest_dir / se.filename
                            sftp.get(remote_file, str(local_path))
                            downloaded += 1
                    if downloaded > 0:
                        print(f"[SFTP] .json trouvés dans {sub}")
                        break

        print(f"[SFTP] Téléchargés: {downloaded} fichier(s) JSON")
    except Exception as e:
        import traceback
        print("[SFTP] ERREUR:", e)
        traceback.print_exc()
    finally:
        try:
            if sftp: sftp.close()
            if transport: transport.close()
        except:
            pass
    return downloaded

def ftp_list_and_download(remote_dir: str, dest_dir: Path) -> int:
    from ftplib import FTP
    print(f"[FTP] Connexion à {FTP_HOST} ...")
    downloaded = 0
    try:
        with FTP(FTP_HOST) as ftp:
            ftp.login(user=FTP_USER, passwd=FTP_PASS)
            ftp.set_pasv(True)
            ftp.cwd(remote_dir)
            files = ftp.nlst()
            print(f"[FTP] Entrées: {len(files)} — {files[:30]}")
            for fname in files:
                if not fname.lower().endswith(".json"): continue
                local = dest_dir / fname
                with open(local, "wb") as f:
                    ftp.retrbinary(f"RETR {fname}", f.write)
                downloaded += 1
        print(f"[FTP] Téléchargés: {downloaded} fichier(s) JSON")
    except Exception as e:
        import traceback
        print("[FTP] ERREUR:", e)
        traceback.print_exc()
    return downloaded

def pull_player_jsons(dest_dir: Path) -> int:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not (FTP_HOST and FTP_USER and FTP_PASS and FTP_PATH):
        print("[CFG] Manque FTP_HOST/FTP_USER/FTP_PASS/FTP_PATH")
        return 0
    if FTP_MODE == "sftp":
        return sftp_list_and_download(FTP_PATH, dest_dir)
    return ftp_list_and_download(FTP_PATH, dest_dir)

# ======= PARSE =======
def load_usercache_map() -> dict:
    m = {}
    f = LOCAL_DATA_DIR / "usercache.json"
    if not f.exists(): return m
    try:
        data = json.load(open(f, "r", encoding="utf-8"))
        for e in data:
            uuid = re.sub(r"[-]", "", e.get("uuid", "")).lower()
            name = e.get("name", "")
            if uuid and name:
                m[uuid] = name
    except Exception:
        pass
    return m

def guess_name(usercache_map: dict, fpath: Path, obj: dict) -> str:
    m = re.search(r"([0-9a-f]{32})", fpath.stem.lower())
    if m:
        uuid = m.group(1)
        if uuid in usercache_map: return usercache_map[uuid]
    for k in ("playerName","name","player"):
        v = obj.get(k)
        if isinstance(v,str) and 1 <= len(v) <= 32: return v
    return fpath.stem[:16]

def count_caught_species(obj: dict) -> int:
    try:
        pokedex = obj.get("pokedex", {})
        if isinstance(pokedex, dict):
            if "caught" in pokedex and isinstance(pokedex["caught"], list):
                return len(set(map(str, pokedex["caught"])))
            if "caughtCount" in pokedex and isinstance(pokedex["caughtCount"], int):
                return int(pokedex["caughtCount"])
    except Exception:
        pass
    for key in ("caughtSpecies","caught_species","capturedSpecies","captured"):
        if key in obj and isinstance(obj[key], list):
            return len(set(map(str, obj[key])))

    # parcours large (FIX: pas de parenthèse en trop ici)
    def walk(o):
        best = 0
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in ("caught","caughtspecies","captured","capturedspecies") and isinstance(v, list):
                    best = max(best, len(set(map(str, v))))
                best = max(best, walk(v))
        elif isinstance(o, list):
            for it in o:
                best = max(best, walk(it))
        return best

    return walk(obj)

def collect_rows() -> List[Tuple[str,int]]:
    rows: List[Tuple[str,int]] = []
    usercache_map = load_usercache_map()
    for f in LOCAL_DATA_DIR.glob("*.json"):
        try:
            data = json.load(open(f, "r", encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        name = guess_name(usercache_map, f, data)
        cnt  = int(count_caught_species(data))
        rows.append((name, cnt))
    rows.sort(key=lambda t: (-t[1], t[0].lower()))
    return rows

# ======= IMAGE =======
def make_image(rows: List[Tuple[str,int]]) -> None:
    W, H = 900, 80 + 60 * max(1, len(rows[:MAX_ROWS]))
    img = Image.new("RGBA", (W, H), (18, 18, 22, 255))
    d = ImageDraw.Draw(img)
    d.text((24, 18), TITLE, font=FONT_TITLE, fill=(255,255,255,255))
    y0 = 80
    d.text((24, y0), "Joueur",   font=FONT_ROW, fill=(200,200,200,255))
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
    print(f"[IMG] Écrit: {OUT_PATH} ({len(rows[:MAX_ROWS])} lignes)")

# ======= LOOP =======
def updater_loop():
    global last_rows
    while True:
        try:
            n = pull_player_jsons(LOCAL_DATA_DIR)
            print(f"[SYNC] JSON rapatriés: {n}")
            last_rows = collect_rows()
            print(f"[BUILD] Joueurs listés: {len(last_rows)}")
            make_image(last_rows)
        except Exception as e:
            import traceback
            print("[LOOP] ERREUR:", e)
            traceback.print_exc()
        time.sleep(REFRESH_S)

# ======= HTTP =======
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
    return {"count": len(last_rows), "rows": last_rows[:MAX_ROWS]}

if __name__ == "__main__":
    Thread(target=updater_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
