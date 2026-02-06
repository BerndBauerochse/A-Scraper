import logging
import threading
import time
import os
import secrets
import math
import json
from datetime import datetime
from threading import Lock
from typing import List, Dict, Optional

# Disable SSL Warnings for internal requests if needed
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from fastapi import FastAPI, BackgroundTasks, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# Internal Modules
from audible_scraper.scraper import AudibleScraper
from audible_scraper.storage import load_entries, save_entries
from audible_scraper.update_manager import UpdateManager
from audible_scraper.models import Entry

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log", mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger("AudibleScraperWeb")

# App Initialization
app = FastAPI()
security = HTTPBasic(auto_error=False)
templates = Jinja2Templates(directory="templates")

# Configuration
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "Audible26")
COOKIE_NAME = "scraper_auth"
SCRAPE_SNAPSHOT_FILE = os.path.join("data", "last_scrape.json")

# Setup Assets
os.makedirs("assets", exist_ok=True)
if os.path.exists("audible_scraper_v2/assets"):
    app.mount("/assets", StaticFiles(directory="audible_scraper_v2/assets"), name="assets")
else:
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# Global State
db_lock = threading.Lock()
entries_map: Dict[str, Entry] = {}
is_running = False
last_message = "Ready"

START_URLS = [
    "https://www.audible.de/search?ref=&searchProvider=Der+Audio+Verlag&sort=pubdate-desc-rank",
    "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290273031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
    "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290274031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
    "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290275031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
    "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290276031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
    "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290277031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title",
    "https://www.audible.de/search?author_author=&feature_seven_browse-bin=16290278031&keywords=&narrator=&publisher=Der+Audio+Verlag&ref=a_search_c1_sort_rd_desc&sort=pubdate-desc-rank&title"
]

def load_data():
    global entries_map
    with db_lock:
        try:
            entries_map = load_entries()
            logger.info(f"Loaded {len(entries_map)} entries from disk.")
        except Exception as e:
            logger.error(f"Failed to load entries: {e}")
            entries_map = {}

def load_scrape_snapshot() -> Dict[str, Dict]:
    if not os.path.exists(SCRAPE_SNAPSHOT_FILE):
        return {}
    try:
        with open(SCRAPE_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_scrape_snapshot(snapshot: Dict[str, Dict]):
    os.makedirs(os.path.dirname(SCRAPE_SNAPSHOT_FILE), exist_ok=True)
    with open(SCRAPE_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

def authenticate(request: Request, creds: Optional[HTTPBasicCredentials] = Depends(security)):
    # 1. Check Cookie (Browser)
    token = request.cookies.get(COOKIE_NAME)
    if token and token == "authorized":
        return True
    
    # 2. Check Basic Auth (n8n / API)
    if creds:
        if secrets.compare_digest(creds.password, WEB_PASSWORD):
            return True

    # 3. Fail
    # Check if this is a browser request (Accepts html)
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        # Redirect browser to login page
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    else:
        # Return 401 for API/n8n so they know to try Basic Auth
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

# --- Tasks ---

def run_full_cycle():
    global is_running, last_message, entries_map
    if is_running:
        return
        
    is_running = True
    last_message = "Starting cycle..."
    
    try:
        # 1. Scrape Audible
        last_message = "Scraping Audible..."
        logger.info(last_message)
        
        scraper = AudibleScraper()
        new_count = 0
        current_time = datetime.now().isoformat()
        prev_snapshot = load_scrape_snapshot()
        new_snapshot = {}
        
        # We process each URL
        all_scraped = []
        for idx, url in enumerate(START_URLS):
            last_message = f"Scraping URL {idx+1}/{len(START_URLS)}..."
            logger.info(last_message)
            scraped_batch = scraper.fetch_all_pages(url)
            all_scraped.extend(scraped_batch)
            
        last_message = f"Scraping done. Processing {len(all_scraped)} items..."
        logger.info(last_message)

        # Update Memory
        with db_lock:
            # Refresh from disk first
            entries_map = load_entries()
            
            changes_log = []
            
            for entry in all_scraped:
                new_snapshot[entry.id] = {
                    "title": entry.title or "",
                    "release_date": entry.release_date or "",
                    "price_without_sub": entry.price_without_sub or ""
                }

                if entry.id not in entries_map:
                    entry.is_new = True
                    entry.is_changed = False
                    entry.first_seen = current_time
                    entry.last_seen = current_time
                    entries_map[entry.id] = entry
                    new_count += 1
                    changes_log.append(f"{current_time} | NEW | {entry.title} ({entry.id})")
                else:
                    existing = entries_map[entry.id]
                    old_title = existing.title
                    existing.last_seen = current_time
                    
                    # Detect Changes
                    diffs = []
                    prev = prev_snapshot.get(entry.id)
                    if prev is not None:
                        prev_release = prev.get("release_date", "")
                        prev_price = prev.get("price_without_sub", "")
                        prev_title = prev.get("title", "")
                        cur_release = entry.release_date or ""
                        cur_price = entry.price_without_sub or ""
                        cur_title = entry.title or ""

                        if prev_release != cur_release:
                            diffs.append(f"Release: {prev_release or '-'}->{cur_release or '-'}")
                        if prev_price != cur_price:
                            diffs.append(f"Price: {prev_price or '-'}->{cur_price or '-'}")
                        if prev_title != cur_title:
                            diffs.append(f"Title: {prev_title or '-'} -> {cur_title or '-'}")
                    
                    # Merge fields
                    existing.title = entry.title
                    existing.price_without_sub = entry.price_without_sub
                    existing.release_date = entry.release_date
                    existing.runtime = entry.runtime
                    existing.rating = entry.rating
                    existing.rating_count = entry.rating_count
                    existing.author = entry.author
                    if entry.subtitle: existing.subtitle = entry.subtitle
                    
                    if diffs:
                         existing.is_changed = True
                         log_entry = f"{current_time} | MOD | {entry.title} ({entry.id}) | {', '.join(diffs)}"
                         changes_log.append(log_entry)
                    else:
                         existing.is_changed = False
            
            save_entries(entries_map)
            save_scrape_snapshot(new_snapshot)
            
            # Write Change Log to Disk
            if changes_log:
                try:
                    with open("data/scraper_history.log", "a", encoding="utf-8") as f:
                        for line in changes_log:
                            f.write(line + "\n")
                except Exception as e:
                    logger.error(f"Failed to write history log: {e}")
            
        last_message = f"Scrape complete. {new_count} new entries."
        logger.info(last_message)

    except Exception as e:
        last_message = f"Error: {str(e)}"
        logger.error(last_message)
    finally:
        is_running = False

def run_n8n_update_task():
    global is_running, last_message, entries_map
    if is_running:
        return
    is_running = True
    last_message = "Starting n8n Update..."
    logger.info(last_message)

    try:
        # Reload map explicitly
        with db_lock:
             current_map = load_entries()
             
        def safe_save_callback(updated_map):
            with db_lock:
                save_entries(updated_map)
                
        manager = UpdateManager(current_map, safe_save_callback)
        # Hook logger
        manager.set_log_callback(logger.info)
        
        success, msg = manager.run_update()
        last_message = f"Done: {msg}"
        logger.info(last_message)
        
        # Final reload
        load_data()
        
    except Exception as e:
        last_message = f"Error: {e}"
        logger.error(last_message)
    finally:
        is_running = False

# --- Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login_submit(request: Request):
    try:
        body = await request.json()
    except:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)
    
    password = body.get("password", "")
    if secrets.compare_digest(password, WEB_PASSWORD):
        response = JSONResponse({"status": "ok"})
        response.set_cookie(key=COOKIE_NAME, value="authorized", httponly=True, max_age=86400*30)
        return response
    else:
        return JSONResponse({"status": "error", "message": "Wrong password"}, status_code=401)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, _auth: bool = Depends(authenticate)):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/entries")
def get_entries(_auth: bool = Depends(authenticate)):
    with db_lock:
        results = []
        for entry in entries_map.values():
            d = entry.to_dict()
            # Add calculated fields
            d['calculated_price'] = entry.calculated_price
            d['runtime_price'] = entry.runtime_price
            
            # AGGRESSIVE SANITIZATION to prevent JSON errors
            safe_d = {}
            for k, v in d.items():
                if v is None:
                    safe_d[k] = ""
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    safe_d[k] = ""
                else:
                    safe_d[k] = v
            results.append(safe_d)
        return results

@app.get("/api/status")
def get_status(_auth: bool = Depends(authenticate)):
    return {
        "is_running": is_running,
        "last_message": last_message,
        "total_entries": len(entries_map)
    }

@app.post("/api/scrape")
def trigger_scrape(background_tasks: BackgroundTasks, _auth: bool = Depends(authenticate)):
    global is_running
    if is_running:
        return {"status": "error", "message": "Task already running"}
    background_tasks.add_task(run_full_cycle)
    return {"status": "ok", "message": "Scrape started"}

@app.post("/api/update_n8n")
def trigger_n8n_update_ep(background_tasks: BackgroundTasks, _auth: bool = Depends(authenticate)):
    global is_running
    if is_running:
        return {"status": "error", "message": "Task already running"}
    background_tasks.add_task(run_n8n_update_task)
    return {"status": "ok", "message": "n8n Update started"}

@app.get("/api/export")
def export_csv(_auth: bool = Depends(authenticate)):
    import csv, io
    stream = io.StringIO()
    writer = csv.writer(stream)
    
    # Header
    writer.writerow([
        "Status", "ID", "Titel", "Untertitel", "Autor", "EAN", 
        "Preis (DB)", "Preis (Laufzeit)", "Preis (Audible)", 
        "VÖ Datum", "Laufzeit", "Bewertung", "Anzahl Bew."
    ])
    
    with db_lock:
        for entry in entries_map.values():
            status = "NEU" if entry.is_new else ("MOD" if entry.is_changed else "")
            writer.writerow([
                status,
                entry.id,
                entry.title,
                entry.subtitle,
                entry.author,
                entry.ean_digital, # FIXED: using ean_digital as per model
                entry.price_digital_de,
                entry.runtime_price,
                entry.price_without_sub,
                entry.release_date,
                entry.runtime,
                entry.rating,
                entry.rating_count
            ])
            
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=audible_export.csv"
    return response

@app.get("/api/logs")
def get_logs(_auth: bool = Depends(authenticate)):
    log_path = "data/scraper_history.log"
    logs = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                logs = f.readlines()
            logs = [l.strip() for l in logs if l.strip()]
            logs.reverse() # Newest first
        except:
            logs = ["Error reading log file"]
    return logs

# --- Startup ---
def schedule_loop():
    while True:
        # Run every 24 hours
        time.sleep(86400)
        logger.info("Automatic schedule triggered.")
        run_full_cycle()

@app.on_event("startup")
def startup_event():
    load_data()
    # Start scheduler thread
    threading.Thread(target=schedule_loop, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5005)
