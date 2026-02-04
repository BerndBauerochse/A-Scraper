import os
import requests
import pandas as pd
import zipfile
import shutil
import time
from datetime import datetime
from typing import Optional, List, Dict, Callable
import tempfile
import warnings

# Suppress openpyxl warnings if any
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

class UpdateManager:
    N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "https://n8n.der-audio-verlag.de/webhook/389acb40-3902-4f50-a3e8-e99f77a7b7ff")

    def __init__(self, entries_map: Dict, save_callback: Callable):
        self.entries_map = entries_map
        self.save_callback = save_callback
        self.log_callback = print 

    def set_log_callback(self, callback):
        self.log_callback = callback

    def log(self, message):
         if self.log_callback:
             self.log_callback(message)

    def run_update(self):
        self.log("Starte Update-Prozess via n8n Webhook...")
        try:
            # 1. Fetch JSON from n8n
            data = self.fetch_n8n_data()
            if not data:
                return False, "Keine Daten von n8n empfangen."

            # 2. Convert to DataFrame
            df = self.convert_to_dataframe(data)
            if df is None or df.empty:
                return False, "Daten konnten nicht verarbeitet werden."

            # 3. Update Database
            updated_count = self.update_database(df)
            
            return True, f"Update erfolgreich. {updated_count} Titel aktualisiert."

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"Fehler beim Update: {str(e)}"

    def fetch_n8n_data(self) -> List[Dict]:
        self.log(f"Rufe Webhook auf: {self.N8N_WEBHOOK_URL}")
        try:
            # Timeout increased for large datasets
            response = requests.get(self.N8N_WEBHOOK_URL, timeout=60, verify=False)
            response.raise_for_status()
            
            json_blob = response.json()
            
            # Helper to normalize list vs dict wrapper
            if isinstance(json_blob, list):
                self.log(f"Empfangen: {len(json_blob)} Einträge.")
                return json_blob
            elif isinstance(json_blob, dict):
                # Check for "data" or "results"
                if "data" in json_blob and isinstance(json_blob["data"], list):
                    self.log(f"Empfangen: {len(json_blob['data'])} Einträge (in 'data').")
                    return json_blob["data"]
                self.log("Empfangen: Einzelnes Objekt.")
                return [json_blob]
            else:
                self.log(f"Unerwartetes Format: {type(json_blob)}")
                return []
                
        except Exception as e:
            self.log(f"Fehler beim Webhook-Aufruf: {e}")
            return []

    def convert_to_dataframe(self, items: List[Dict]) -> pd.DataFrame:
        self.log("Konvertiere JSON zu Struktur...")
        rows = []
        for item in items:
            # Flexible mapping based on what Renamer sees + UpdateManager needs
            
            # Map EAN
            ean = item.get("EAN") or item.get("EAN_digital")
            if not ean: continue
            
            # Map Link
            # Renamer uses 'shoplink_audible' or 'Shoplink Audible'
            link = item.get("Shoplink Audible") or item.get("shoplink_audible") or item.get("Shoplink_Audible")
            
            # Map Price (Optional)
            # Keys might be 'Preis digital DE' or just 'Preis' or 'price'
            price = item.get("Preis digital DE") or item.get("Preis") or item.get("price")
            
            rows.append({
                "Shoplink Audible": str(link) if link else "",
                "EAN digital": str(ean),
                "Preis digital DE": price if price else ""
            })
            
        df = pd.DataFrame(rows)
        
        # Cleanup
        # EAN: remove .0
        def clean_ean(val):
            s = str(val).strip()
            if s.endswith(".0"): return s[:-2]
            return s
        df["EAN digital"] = df["EAN digital"].apply(clean_ean)
        
        # Link: strip
        df["Shoplink Audible"] = df["Shoplink Audible"].astype(str).str.strip()
        
        # Price: format if float
        def clean_price(val):
            if not val: return ""
            if isinstance(val, (int, float)):
                 return f"{val:.2f}".replace(".", ",") + " €"
            return str(val).strip()
        df["Preis digital DE"] = df["Preis digital DE"].apply(clean_price)
        
        return df

    def save_backup(self, df: pd.DataFrame):
        backup_path = os.path.join("data", "import_backup.xlsx")
        os.makedirs("data", exist_ok=True)
        self.log(f"Speichere Backup unter: {backup_path}")
        try:
            df.to_excel(backup_path, index=False)
        except Exception as e:
            self.log(f"Warnung: Backup konnte nicht gespeichert werden: {e}")

    def update_database(self, df: pd.DataFrame) -> int:
        self.log("Aktualisiere Datenbank...")
        updated_count = 0
        
        # Create a lookup map for the DataFrame for faster access
        # Key: Shoplink Audible (normalized)
        # Value: Row
        
        # We need to match "Shoplink Audible" from Excel with "url" from Entry
        if df is None or df.empty:
            return 0
            
        # SANITIZE: Replace all NaN with empty string to prevent JSON errors
        df = df.fillna("")
            
        total_updated = 0
        
        # Helper to extract ASIN (Standard B0... or 10 chars)
        import re
        def extract_asin(u):
            if not u: return ""
            # Look for 10-char alphanum ID, usually starting with B
            match = re.search(r'([A-Z0-9]{10})', str(u))
            if match:
                return match.group(1)
            return ""
            
        # Build lookup from Excel/DataFrame
        excel_lookup = {}
        for idx, row in df.iterrows():
            link = row["Shoplink Audible"]
            asin = extract_asin(link)
            if asin:
                excel_lookup[asin] = row
            
            # Also keep exact link just in case
            if link and str(link).lower() != "nan":
                excel_lookup[str(link).strip()] = row

        # Iterate over existing entries
        for entry_id, entry in self.entries_map.items():
            match_row = None
            
            # 1. Try ASIN Match
            entry_asin = extract_asin(entry.url)
            if entry_asin and entry_asin in excel_lookup:
                match_row = excel_lookup[entry_asin]
            
            # 2. Try Exact URL Match (Fallback)
            if match_row is None and entry.url in excel_lookup:
                 match_row = excel_lookup[entry.url]
            
            if match_row is not None:
                # Found a match!
                changed = False
                
                # Update EAN if missing
                new_ean = match_row["EAN digital"]
                if not entry.ean and new_ean:
                    entry.ean = new_ean
                    changed = True
                elif entry.ean and new_ean and entry.ean != new_ean:
                     # User said: "1 der 'EAN digital' falls sie noch nicht vorhanden ist"
                     # So if it IS present, do we overwrite? "falls sie noch nicht vorhanden ist" implies NO.
                     # But usually updates imply overwriting.
                     # "falls sie noch nicht vorhanden ist" -> If NOT present.
                     pass

                # Update Price
                # "nach dem 'Preis digital DE' der den Preis LZ ersetzten soll"
                # This implies ALWAYS replace/set.
                new_price = match_row["Preis digital DE"]
                if new_price:
                    if entry.price_digital_de != new_price:
                        entry.price_digital_de = new_price
                        changed = True
                
                if changed:
                    updated_count += 1

        if updated_count > 0:
            self.save_callback(self.entries_map)
            
        return updated_count
