import json
import os
from typing import Dict, List
from .models import Entry

DATA_FILE = os.path.join("data", "audible_entries.json")
LOG_FILE = os.path.join("data", "scrape_log.json")

def load_entries() -> Dict[str, Entry]:
    """Loads entries from the JSON file."""
    if not os.path.exists(DATA_FILE):
        return {}
    
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {entry_id: Entry.from_dict(entry_data) for entry_id, entry_data in data.items()}
    except (json.JSONDecodeError, IOError):
        return {}

def save_entries(entries: Dict[str, Entry]):
    """Saves entries to the JSON file."""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    data = {entry_id: entry.to_dict() for entry_id, entry in entries.items()}

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_log() -> List[Dict]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def append_log_entry(entry: Dict):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    log = load_log()
    log.insert(0, entry)  # newest first
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
