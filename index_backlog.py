import os
import json
import requests
import base64
import subprocess
import time
import io

# --- Configuration ---
IMAGE_FOLDER = r"F:\Organised_Photos\2025\04\09"
SERVER_URL = "http://127.0.0.1:1234/v1/chat/completions" 
MODEL_NAME = "qwen/qwen3-vl-8b"
EXIFTOOL_PATH = r"c:\Users\James\Documents\coding\wildlife projecxts\exiftool.exe"
INDEX_FILE = r"c:\Users\James\Documents\coding\wildlife projecxts\animal_index.json"
RAW_EXTENSIONS = ('.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf')
MAX_IMAGE_SIDE = 1280  # VL models downsample anyway; huge previews just cost encode + vision tokens

def extract_preview_jpeg(raw_path):
    cmd = [EXIFTOOL_PATH, "-fast2", "-b", "-PreviewImage", raw_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode != 0 or not result.stdout:
        cmd_fallback = [EXIFTOOL_PATH, "-fast2", "-b", "-JpgFromRaw", raw_path]
        result = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode == 0 and result.stdout:
        return result.stdout
    raise Exception("ExifTool image conversion failed.")

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

# Load database progress safely
if os.path.exists(INDEX_FILE):
    with open(INDEX_FILE, 'r') as f:
        database = json.load(f)
    print(f"Loaded existing database index. {len(database)} images currently processed.")
else:
    database = {}
    print("Starting a completely fresh database index.")

all_images = [f for f in os.listdir(IMAGE_FOLDER) if f.lower().endswith(RAW_EXTENSIONS)]
images_to_process = [img for img in all_images if img not in database]

print(f"Found {len(all_images)} total images in folder. Processing remaining {len(images_to_process)} files...")

http = requests.Session()

for idx, img_name in enumerate(images_to_process, 1):
    img_path = os.path.join(IMAGE_FOLDER, img_name)
    print(f"[{idx}/{len(images_to_process)}] Analyzing: {img_name}...")
    
    try:
        base64_image = jpeg_to_base64(extract_preview_jpeg(img_path))
        
        prompt_text = (
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
        
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }
        
        response = http.post(SERVER_URL, json=payload, timeout=90)
        response.raise_for_status()
        response_data = response.json()
        
        if 'error' not in response_data:
            # FIXED: Correctly tracking choices as a list index array block [0]
            raw_content = response_data['choices'][0]['message']['content'].strip()
            
            # Sanitize raw string layout formats
            if raw_content.startswith("```"):
                lines = raw_content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw_content = "\n".join(lines).strip()
            if raw_content.startswith("json"):
                raw_content = raw_content.split("json", 1)[1].strip()
                
            parsed_json = json.loads(raw_content)
            
            database[img_name] = {
                "filename": img_name,
                "file_path": os.path.abspath(img_path),
                "animal_label": parsed_json.get("animal_label", "Unknown").strip(),
                "scene_type": parsed_json.get("scene_type", "single_subject"),
                "requires_manual_check": parsed_json.get("requires_manual_check", False),
                "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
            status_flag = "⚠️ REVIEW NEEDED" if database[img_name]["requires_manual_check"] else "✅ OK"
            print(f"   ↳ Labeled: {database[img_name]['animal_label']} | Type: {database[img_name]['scene_type']} | [{status_flag}]")
            
            with open(INDEX_FILE, "w") as f:
                json.dump(database, f, indent=4)
        else:
            print(f"   ↳ ❌ Server error on {img_name}: {response_data['error']}")
            
    except Exception as e:
        print(f"   ↳ ❌ Extraction/Network parse failure on {img_name}: {e}")
        time.sleep(1)

print(f"\n🎉 Backlog catalog processing complete! Master index successfully updated at: {INDEX_FILE}")