import os
import json
import hashlib
import datetime
import subprocess
import tempfile
import threading
import requests
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

# ============================
# إعدادات JSONBin والمفتاح السري للمشرف
# ============================
JSONBIN_ID = "68f4ef18ae596e708f1cc0d9"
JSONBIN_KEY = "$2a$10$BV..TadGPZnl8Hs6rUs4h.kJFEnRDmK6YPqd8onbIEhfCKSixLI66"
JSONBIN_BASE = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"
ADMIN_SECRET_KEY = "RESIS_TIK_PRO1"

if not ADMIN_SECRET_KEY:
    raise ValueError("⛔️ خطأ فادح: ADMIN_SECRET_KEY غير معين.")

_jsonbin_session = requests.Session()
_jsonbin_session.headers.update({
    "X-Master-Key": JSONBIN_KEY,
    "Content-Type": "application/json; charset=utf-8"
})
DB_LOCK = threading.Lock()

# ============================
# دوال التخزين (Database Functions)
# ============================
def load_db():
    with DB_LOCK:
        try:
            r = _jsonbin_session.get(JSONBIN_BASE)
            if r.status_code == 404:
                return {"codes": []}
            r.raise_for_status()
            data = r.json().get("record", {"codes":[]})
            if "codes" not in data:
                data["codes"] = []
            return data
        except (requests.exceptions.RequestException, json.JSONDecodeError):
            return {"codes": []}

def save_db(data):
    with DB_LOCK:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        r = _jsonbin_session.put(JSONBIN_BASE, data=payload)
        r.raise_for_status()

def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def hash_device(device: str):
    return hashlib.sha256(device.encode("utf-8")).hexdigest()

def find_key(db, key: str):
    for row in db.get("codes", []):
        if row.get("key") == key:
            return row
    return None

def calc_expiry(activated_on_iso: str | None, duration_days: int | None):
    if not activated_on_iso:
        return None
    try:
        activated = datetime.datetime.strptime(activated_on_iso, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
    days = int(duration_days or 30)
    return activated + datetime.timedelta(days=days)

def ensure_bound_or_bind(db, row, device: str, device_name: str | None):
    dev_hash = hash_device(device)
    if not row.get("device_hash"):
        row["device_hash"] = dev_hash
        row["device_name"] = device_name
        if not row.get("activated_on"): row["activated_on"] = now_iso()
        save_db(db)
        return True
    return row["device_hash"] == dev_hash

# ============================
# إعداد التطبيق (App Setup)
# ============================
app = FastAPI(title="4TIK PRO Service API")
BASE_DIR = Path(__file__).resolve().parent

# ============================
# خطط المعالجة (Processing Plans)  ✅ (الإضافة)
# ============================
FFMPEG_PLANS = {
    # سريع (كما هو عندك)
    "fast": ["ffmpeg", "-itsscale", "2", "-i", "{input}", "-c:v", "copy", "-c:a", "copy", "{output}"],

    # محسّن ✨ (سلاسة + تحسين بسيط للجودة)
    "smooth": [
        "ffmpeg", "-i", "{input}",
        "-vf", "fps=60,hqdn3d=1.2:1.2:6:6,unsharp=5:5:0.7:3:3:0.4,tmix=frames=2:weights='1 1',eq=contrast=1.05:saturation=1.08:brightness=0.01",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-g", "120", "-keyint_min", "60", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        "{output}"
    ],

    # احترافي 🔥 (أعلى جودة — وقت أطول)
    "ultra": [
        "ffmpeg", "-i", "{input}",
        "-vf", "fps=60,hqdn3d=1.2:1.2:6:6,unsharp=5:5:0.7:3:3:0.4,tmix=frames=2:weights='1 1',eq=contrast=1.05:saturation=1.08:brightness=0.01",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-g", "120", "-keyint_min", "60", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        "{output}"
    ]
}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================
# حماية حجم الملف (250MB)
# ============================
MAX_BYTES = 250 * 1024 * 1024

async def verify_content_length(content_length: int = Header(...)):
    if content_length > MAX_BYTES:
        raise HTTPException(status_code=413, detail="الملف أكبر من 250MB")

# ============================
# صفحات (لخمس ملفات فقط)
# ============================
@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(str(BASE_DIR / "index.html"))

@app.get("/login.html", include_in_schema=False)
async def login_page():
    return FileResponse(str(BASE_DIR / "login.html"))

@app.get("/index.html", include_in_schema=False)
async def index_page():
    return FileResponse(str(BASE_DIR / "index.html"))

# ============================
# إضافة اشتراك من المتصفح (كما هو)
# ============================
@app.get("/subscribe", summary="إضافة اشتراك من المتصفح مباشرة")
async def add_subscription(key: str, duration_days: int = 30, admin_key: str = ""):
    if admin_key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="مفتاح المشرف غير صحيح")

    db = load_db()

    if find_key(db, key):
        return JSONResponse(
            content={"message": f"المفتاح '{key}' موجود مسبقًا."},
            media_type="application/json; charset=utf-8"
        )

    new_key = {
        "key": key,
        "duration_days": duration_days,
        "activated_on": None,
        "device_hash": "",
        "device_name": None,
        "last_used": None
    }
    db["codes"].append(new_key)
    save_db(db)

    return {"message": f"تم إضافة المفتاح '{key}' بنجاح ✅", "duration_days": duration_days}

# ============================
# معلومات الاشتراك الحالية (كما هو)
# ============================
@app.get("/me", summary="الحصول على معلومات الاشتراك الحالية")
async def me(request: Request):
    key = request.headers.get("X-KEY")
    device = request.headers.get("X-DEVICE")
    device_name = request.headers.get("X-DEVICE-NAME")

    if not key or not device:
        raise HTTPException(status_code=401, detail="المفتاح (X-KEY) ومعرف الجهاز (X-DEVICE) مطلوبان")

    db = load_db()
    row = find_key(db, key)
    if not row:
        raise HTTPException(status_code=404, detail="المفتاح غير صالح")

    if not ensure_bound_or_bind(db, row, device, device_name):
        raise HTTPException(status_code=403, detail="هذا المفتاح مربوط بجهاز آخر")

    expires_on = calc_expiry(row.get("activated_on"), row.get("duration_days", 30))
    now = datetime.datetime.utcnow()
    is_expired = (expires_on is None) or (now >= expires_on)
    days_left = 0 if is_expired else ((expires_on - now).days if expires_on else row.get("duration_days", 30))

    row["last_used"] = now_iso()
    save_db(db)

    return {
        "key_masked": row["key"][:4] + "****",
        "device_name": row.get("device_name"),
        "activated_on": row.get("activated_on"),
        "expires_on": expires_on.isoformat() if expires_on else None,
        "days_left": days_left,
        "is_active": not is_expired,
        "last_used": row.get("last_used")
    }

# ============================
# معالجة الفيديو (تمت إضافة plan فقط ✅)
# ============================
@app.post("/process", summary="معالجة الفيديو للمستخدمين المشتركين", dependencies=[Depends(verify_content_length)])
async def process_video(request: Request, file: UploadFile = File(...), plan: str = Form("fast")):
    key = request.headers.get("X-KEY")
    device = request.headers.get("X-DEVICE")
    if not key or not device:
        raise HTTPException(status_code=401, detail="FUCK OFF BITCH 🖕")

    db = load_db()
    row = find_key(db, key)
    if not row:
        raise HTTPException(status_code=401, detail="المفتاح غير صحيح")

    if not ensure_bound_or_bind(db, row, device, None):
        raise HTTPException(status_code=403, detail="المفتاح مربوط بجهاز آخر")

    expires_on = calc_expiry(row.get("activated_on"), row.get("duration_days", 30))
    if not expires_on or datetime.datetime.utcnow() >= expires_on:
        raise HTTPException(status_code=403, detail="⛔ انتهت صلاحية هذا المفتاح")

    row["last_used"] = now_iso()
    save_db(db)

    tmp_in_path = None
    tmp_out_path = None

    try:
        suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
    tmp_in_path = tmp_in.name

with open(tmp_in_path, "wb") as f:
    while True:
        chunk = await file.read(1024 * 1024)  # 1MB
        if not chunk:
            break
        f.write(chunk)

        tmp_out_path = tmp_in_path.replace(suffix, f"_out{suffix}")

        if plan not in FFMPEG_PLANS:
            plan = "fast"

        cmd = [c.format(input=tmp_in_path, output=tmp_out_path) for c in FFMPEG_PLANS[plan]]
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8')

        return FileResponse(tmp_out_path, filename=f"4tik_{file.filename}")

    except subprocess.CalledProcessError:
        next_plan = "smooth" if plan == "fast" else ("ultra" if plan == "smooth" else None)
        return JSONResponse(status_code=400, content={"status": "failed", "message": "فشلت المعالجة", "next_plan": next_plan})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"حدث خطأ غير متوقع: {str(e)}")

    finally:
        try:
            if tmp_in_path and os.path.exists(tmp_in_path):
                os.remove(tmp_in_path)
        except:
            pass

        try:
            if tmp_out_path and os.path.exists(tmp_out_path):
                os.remove(tmp_out_path)
        except:
            pass
