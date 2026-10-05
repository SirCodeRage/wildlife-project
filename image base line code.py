import os
import json
import requests
import base64
import subprocess
import time

# --- Configuration ---
IMAGE_FOLDER = r"F:\test 4"
# FIXED: Pointed directly back to the active multimodal worker port from your logs
SERVER_URL = "http://127.0.0" 
MODEL_NAME = "qwen/qwen3-vl-8b"
EXIFTOOL_PATH = r"c:\Users\James\Documents\coding\wildlife projecxts\exiftool.exe"
INDEX_FILE = r"c:\Users\James\Documents\coding\wildlife projecxts\animal_index.json"
RAW_EXTENSIONS = ('.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf')

def extract_preview_base64_exiftool(raw_path):
    cmd = [EXIFTOOL_PATH, "-b", "-PreviewImage", raw_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0 or not result.stdout:
        cmd_fallback = [EXIFTOOL_PATH, "-b", "-JpgFromRaw", raw_path]
        result = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0 and result.stdout:
        return base64.b64encode(result.stdout).decode('utf-8')
    else:
        raise Exception("ExifTool image conversion failed.")

# Load database progress safely to handle picking back up after interruptions
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

for idx, img_name in enumerate(images_to_process, 1):
    img_path = os.path.join(IMAGE_FOLDER, img_name)
    print(f"[{idx}/{len(images_to_process)}] Analyzing: {img_name}...")
    
    try:
        base64_image = extract_preview_base64_exiftool(img_path)
        
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
            "temperature": 0.1
        }
        
        response = requests.post(SERVER_URL, json=payload, timeout=90)
        response.raise_for_status()
        response_data = response.json()
        
        if 'error' not in response_data:
            raw_content = response_data['choices']['message']['content'].strip()
            
            # Sanitize raw text data outputs
            if raw_content.startswith("```"):
                raw_content = raw_content.split("\n", 1).rsplit("\n", 1).strip()
            if raw_content.startswith("json"):
                raw_content = raw_content.split("json", 1).strip()
                
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
            
            # Save instantly to disk incrementally so data is never lost if execution stops
            with open(INDEX_FILE, "w") as f:
                json.dump(database, f, indent=4)
        else:
            print(f"   ↳ ❌ Server error on {img_name}: {response_data['error']}")
            
    except Exception as e:
        print(f"   ↳ ❌ Extraction/Network parse failure on {img_name}: {e}")
        time.sleep(1)

print(f"\n🎉 Backlog catalog processing complete! Master index successfully updated at: {INDEX_FILE}")
