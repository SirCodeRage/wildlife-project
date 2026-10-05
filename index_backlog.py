import os
import json
import requests
import base64
import subprocess
import time
import io
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration ---
IMAGE_FOLDER = r"F:\Organised_Photos\2025\04\09"
SERVER_URL = "http://127.0.0.1:1234/v1/chat/completions"
MODEL_NAME = "qwen/qwen3-vl-8b"
EXIFTOOL_PATH = r"c:\Users\James\Documents\coding\wildlife projecxts\exiftool.exe"
FFMPEG_PATH = r"C:\ffmpeg.exe"  # HDR/HEVC CR3 previews are not JPEG
INDEX_FILE = r"c:\Users\James\Documents\coding\wildlife projecxts\animal_index.json"
RAW_EXTENSIONS = ('.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf')
MAX_IMAGE_SIDE = 1280  # VL models downsample anyway; huge previews just cost encode + vision tokens
MAX_WORKERS = 2  # 1 = old sequential runs; 2 overlaps the next extract with the VL call

PROMPT_TEXT = (
    "You are a professional British wildlife cataloguer. Analyze this photograph.\n"
    "Identify the single primary/foreground subject. If the scene contains a dense, mixed cluster of birds or if "
    "the animal is too far away/blurry to classify reliably, set requires_manual_check to true.\n\n"
    "Return ONLY a clean JSON object codeblock matching the structure below. Do not wrap it in markdown text wrappers:\n"
    "{\n"
    '  "animal_label": "Common Name of primary subject or Unknown",\n'
    '  "scene_type": "single_subject" or "flock_cluster" or "distant_subject",\n'
    '  "requires_manual_check": true or false\n'
    "}"
)

print_lock = threading.Lock()
db_lock = threading.Lock()
exiftool_lock = threading.Lock()
_ffmpeg_lock = threading.Lock()
_thread_local = threading.local()
PREVIEW_TAGS = ("PreviewImage", "JpgFromRaw", "OtherImage", "ThumbnailImage")
_ffmpeg_path = None
_ffmpeg_checked = False


def get_http():
    http = getattr(_thread_local, "http", None)
    if http is None:
        http = requests.Session()
        _thread_local.http = http
    return http


def log(msg):
    with print_lock:
        print(msg)


def find_ffmpeg():
    global _ffmpeg_path, _ffmpeg_checked
    with _ffmpeg_lock:
        if _ffmpeg_checked:
            return _ffmpeg_path
        _ffmpeg_checked = True
        dirname = os.path.dirname(EXIFTOOL_PATH)
        candidates = [
            FFMPEG_PATH,
            shutil.which(FFMPEG_PATH),
            shutil.which("ffmpeg"),
            shutil.which("ffmpeg.exe"),
            os.path.join(dirname, "ffmpeg.exe"),
            os.path.join(dirname, "ffmpeg"),
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                _ffmpeg_path = candidate
                return _ffmpeg_path
            # Windows .exe from an explicit path may not report X_OK the same way
            if candidate and os.path.isfile(candidate) and candidate.lower().endswith(".exe"):
                _ffmpeg_path = candidate
                return _ffmpeg_path
        _ffmpeg_path = None
        return None


def _hvcc_param_nals(hvcc_payload):
    if len(hvcc_payload) < 23:
        return []
    num_arrays = hvcc_payload[22]
    pos = 23
    nals = []
    for _ in range(num_arrays):
        if pos + 3 > len(hvcc_payload):
            return []
        num_nalus = int.from_bytes(hvcc_payload[pos + 1:pos + 3], "big")
        pos += 3
        for _ in range(num_nalus):
            if pos + 2 > len(hvcc_payload):
                return []
            nal_len = int.from_bytes(hvcc_payload[pos:pos + 2], "big")
            pos += 2
            nals.append(hvcc_payload[pos:pos + nal_len])
            pos += nal_len
    return nals


def _imgd_nals(imgd_payload):
    # Canon IMGD: 4-byte outer length, then length-prefixed HEVC NALUs.
    pos = 4
    nals = []
    while pos + 4 <= len(imgd_payload):
        nlen = int.from_bytes(imgd_payload[pos:pos + 4], "big")
        pos += 4
        if nlen <= 0 or pos + nlen > len(imgd_payload):
            return []
        nals.append(imgd_payload[pos:pos + nlen])
        pos += nlen
    return nals


def _cr3_hevc_annexb(raw_bytes):
    start_code = b"\x00\x00\x00\x01"
    for fourcc in (b"PRVW", b"THMB"):
        type_pos = raw_bytes.find(fourcc)
        if type_pos < 4:
            continue
        start = type_pos - 4
        size = int.from_bytes(raw_bytes[start:start + 4], "big")
        if size < 32 or start + size > len(raw_bytes):
            continue
        end = start + size
        hvcc_type = raw_bytes.find(b"hvcC", start, end)
        imgd_type = raw_bytes.find(b"IMGD", start, end)
        if hvcc_type < 4 or imgd_type < 4:
            continue
        hvcc_pos = hvcc_type - 4
        imgd_pos = imgd_type - 4
        hvcc_size = int.from_bytes(raw_bytes[hvcc_pos:hvcc_pos + 4], "big")
        imgd_size = int.from_bytes(raw_bytes[imgd_pos:imgd_pos + 4], "big")
        if hvcc_pos + hvcc_size > end or imgd_pos + imgd_size > end:
            continue
        params = _hvcc_param_nals(raw_bytes[hvcc_pos + 8:hvcc_pos + hvcc_size])
        pictures = _imgd_nals(raw_bytes[imgd_pos + 8:imgd_pos + imgd_size])
        if not params or not pictures:
            continue
        return b"".join(start_code + nal for nal in params + pictures)
    return None


def _ffmpeg_hevc_to_jpeg(annexb):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise Exception(
            "CR3 preview is HEVC (not JPEG). Install ffmpeg and put it on PATH, or set FFMPEG_PATH."
        )
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryDirectory() as tmp:
        h265_path = os.path.join(tmp, "preview.h265")
        jpg_path = os.path.join(tmp, "preview.jpg")
        with open(h265_path, "wb") as handle:
            handle.write(annexb)
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "hevc", "-i", h265_path,
             "-frames:v", "1", "-q:v", "2", jpg_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        if result.returncode != 0 or not os.path.isfile(jpg_path):
            err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            raise Exception(f"ffmpeg HEVC preview decode failed. {err}")
        with open(jpg_path, "rb") as handle:
            jpeg_bytes = handle.read()
        if not jpeg_bytes.startswith(b"\xff\xd8"):
            raise Exception("ffmpeg HEVC preview decode failed. (output was not JPEG)")
        return jpeg_bytes


def extract_preview_jpeg(raw_path):
    # Windows exiftool.exe unpacks to a shared temp dir; parallel copies collide.
    last_err = ""
    try:
        with exiftool_lock:
            for tag in PREVIEW_TAGS:
                result = subprocess.run(
                    [EXIFTOOL_PATH, "-b", f"-{tag}", raw_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                jpeg_bytes = result.stdout or b""
                if jpeg_bytes.startswith(b"\xff\xd8"):
                    return jpeg_bytes
                err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
                if err:
                    last_err = err
    except FileNotFoundError:
        last_err = f"exiftool not found: {EXIFTOOL_PATH}"

    with open(raw_path, "rb") as handle:
        raw_bytes = handle.read()
    annexb = _cr3_hevc_annexb(raw_bytes)
    if annexb:
        return _ffmpeg_hevc_to_jpeg(annexb)

    detail = f" ({last_err})" if last_err else ""
    raise Exception(f"ExifTool image conversion failed.{detail}")


def jpeg_to_base64(jpeg_bytes):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(jpeg_bytes))
        if max(im.size) > MAX_IMAGE_SIDE:
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            jpeg_bytes = buf.getvalue()
    except Exception:
        pass
    return base64.b64encode(jpeg_bytes).decode("utf-8")


def strip_model_json(raw_content):
    raw_content = raw_content.strip()
    if raw_content.startswith("```"):
        lines = raw_content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw_content = "\n".join(lines).strip()
    if raw_content.startswith("json"):
        raw_content = raw_content.split("json", 1)[1].strip()
    return raw_content


def save_record(img_name, record):
    with db_lock:
        database[img_name] = record
        with open(INDEX_FILE, "w") as f:
            json.dump(database, f, indent=4)


def process_image(idx, img_name, total):
    img_path = os.path.join(IMAGE_FOLDER, img_name)
    log(f"[{idx}/{total}] Analyzing: {img_name}...")

    try:
        base64_image = jpeg_to_base64(extract_preview_jpeg(img_path))
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT_TEXT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }

        response = get_http().post(SERVER_URL, json=payload, timeout=90)
        response.raise_for_status()
        response_data = response.json()

        if 'error' not in response_data:
            raw_content = strip_model_json(response_data['choices'][0]['message']['content'])
            parsed_json = json.loads(raw_content)
            record = {
                "filename": img_name,
                "file_path": os.path.abspath(img_path),
                "animal_label": parsed_json.get("animal_label", "Unknown").strip(),
                "scene_type": parsed_json.get("scene_type", "single_subject"),
                "requires_manual_check": parsed_json.get("requires_manual_check", False),
                "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_record(img_name, record)
            status_flag = "⚠️ REVIEW NEEDED" if record["requires_manual_check"] else "✅ OK"
            log(f"   ↳ Labeled: {record['animal_label']} | Type: {record['scene_type']} | [{status_flag}]")
        else:
            log(f"   ↳ ❌ Server error on {img_name}: {response_data['error']}")

    except Exception as e:
        log(f"   ↳ ❌ Extraction/Network parse failure on {img_name}: {e}")
        time.sleep(1)


database = {}


def main():
    global database
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'r') as f:
            database = json.load(f)
        print(f"Loaded existing database index. {len(database)} images currently processed.")
    else:
        database = {}
        print("Starting a completely fresh database index.")

    all_images = [f for f in os.listdir(IMAGE_FOLDER) if f.lower().endswith(RAW_EXTENSIONS)]
    images_to_process = [img for img in all_images if img not in database]
    total = len(images_to_process)

    print(f"Found {len(all_images)} total images in folder. Processing remaining {total} files...")

    if total:
        workers = max(1, min(MAX_WORKERS, total))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(process_image, idx, img_name, total)
                for idx, img_name in enumerate(images_to_process, 1)
            ]
            for fut in as_completed(futures):
                fut.result()

    print(f"\n🎉 Backlog catalog processing complete! Master index successfully updated at: {INDEX_FILE}")


if __name__ == "__main__":
    main()
