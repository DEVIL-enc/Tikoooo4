import os
import json
import datetime
import subprocess
import tempfile
import threading
import requests
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import FileResponse, JSONResponse

# =========================
# JSONBIN SETTINGS (كما هي)
# =========================
JSONBIN_ID = "PUT_YOUR_BIN_ID"
JSONBIN_KEY = "PUT_YOUR_MASTER_KEY"
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

HEADERS = {
    "X-Master-Key": JSONBIN_KEY,
    "Content-Type": "application/json"
}

LOCK = threading.Lock()
BASE_DIR = Path(__file__).resolve().parent
MAX_BYTES = 250 * 1024 * 1024  # 250MB

# =========================
# DATABASE (كما هو)
# =========================
def load_db():
    with LOCK:
        r = requests.get(JSONBIN_URL, headers=HEADERS)
        data = r.json().get("record", {"codes": []})
        return data

def save_db(data):
    with LOCK:
        requests.put(JSONBIN_URL, headers=HEADERS, data=json.dumps(data))

def now():
    return datetime.datetime.utcnow()

def find_key(db, key):
    for k in db["codes"]:
        if k["key"] == key:
            return k
    return None

def is_expired(row):
    if not row.get("activated_on"):
        return True
    start = datetime.datetime.fromisoformat(row["activated_on"])
    return now() > start + datetime.timedelta(days=row.get("duration_days", 30))

# =========================
# FFMPEG PLANS (الإضافة الوحيدة)
# =========================
FFMPEG_PLANS = {
    "fast": [
        "ffmpeg", "-itsscale", "2",
        "-i", "{input}",
        "-c:v", "copy",
        "-c:a", "copy",
        "{output}"
    ],
    "smooth": [
        "ffmpeg", "-i", "{input}",
        "-vf", "fps=60,hqdn3d=1.2:1.2:6:6,unsharp=5:5:0.7",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "{output}"
    ],
    "ultra": [
        "ffmpeg", "-i", "{input}",
        "-vf", "fps=60,hqdn3d=1.5:1.5:8:8,unsharp=5:5:1.0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "{output}"
    ]
}

# =========================
# FASTAPI
# =========================
app = FastAPI()

@app.get("/")
def home():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/login.html")
def login():
    return FileResponse(BASE_DIR / "login.html")

@app.get("/me")
def me(request: Request):
    key = request.headers.get("X-KEY")
    device = request.headers.get("X-DEVICE")

    if not key or not device:
        raise HTTPException(401)

    db = load_db()
    row = find_key(db, key)
    if not row:
        raise HTTPException(401)

    if row.get("device_id") and row["device_id"] != device:
        raise HTTPException(403)

    if not row.get("device_id"):
        row["device_id"] = device
        row["activated_on"] = now().isoformat()
        save_db(db)

    if is_expired(row):
        raise HTTPException(403)

    return {"status": "ok"}

@app.post("/process")
async def process_video(
    request: Request,
    file: UploadFile = File(...),
    plan: str = Form("fast")
):
    key = request.headers.get("X-KEY")
    device = request.headers.get("X-DEVICE")

    db = load_db()
    row = find_key(db, key)
    if not row or is_expired(row):
        raise HTTPException(403)

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413)

    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fin:
        fin.write(data)
        input_path = fin.name

    output_path = input_path.replace(suffix, "_out.mp4")
    cmd = [x.format(input=input_path, output=output_path)
           for x in FFMPEG_PLANS.get(plan, FFMPEG_PLANS["fast"])]

    subprocess.run(cmd, check=True)
    return FileResponse(output_path, filename="output.mp4")
