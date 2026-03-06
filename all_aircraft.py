import telebot
import requests
import time
import math
import threading
from datetime import datetime, timedelta
import gzip
import sys
import argparse

# ============== CONFIG ==============
AIRCRAFT_URL = "http://adsbexchange.local/tar1090/data/aircraft.json"
RECEIVER_URL = "http://adsbexchange.local/tar1090/data/receiver.json"
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"

# Channels
CIVIL_CHAT_ID = "-100000000000"     # Channel for all non-military aircraft
MILITARY_CHAT_ID = "-100000000000"  # Channel for military ONLY

# Toggles
ENABLE_CIVIL = True
ENABLE_MILITARY = True

FEED_ID = "YOUR_ADSB_EXCHANGE_FEED_ID" # ← your personal ADS-B Exchange feed ID
POLL_INTERVAL = 18

# FILTERS (change if you want less spam)
MIN_ALTITUDE_FT = None      # e.g. 1500
MAX_DISTANCE_KM = None      # e.g. 80

# Receiver Location Fallback (used if auto-detect fails)
# E.g. 40.7128, -74.0060
RECEIVER_LAT_FALLBACK = None
RECEIVER_LON_FALLBACK = None

# Update retry config
UPDATE_RETRIES = 3
UPDATE_INTERVAL = 10        # seconds between update attempts

# =======================================

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Known Military Aircraft Types (ICAO codes mostly)
MILITARY_AIRCRAFT_TYPES = {
    "A10", "B1", "B2", "B52", "C17", "C5", "C130", "C135", "EC135", "KC135", "RC135",
    "E3TF", "E4", "E6", "E8", "F15", "F16", "F18", "F22", "F35", "T38", "U2", "V22", "MV22",
    "CV22", "AH64", "CH47", "UH60", "MH60", "SH60", "HH60", "MQ9", "RQ4", "T6", "T1", "C21",
    "C40", "C32", "C37", "P8", "P3", "AC13", "MC13", "HC13", "E2", "C2", "A400", "A332", 
    "EUFI", "TORN", "HAWK", "GRIP", "M295", "SU27", "SU35", "MIG29"
}

# Known Military Owner Keywords
MILITARY_OWNER_KEYWORDS = [
    "AIR FORCE", "NAVY", "ARMY", "MARINE", "COAST GUARD", "DEPARTMENT OF DEFENSE",
    "DEFENCE", "RAF", "RCAF", "LUFTWAFFE", "ARMAND", "AERONAUTICA", "FUERZA AEREA",
    "MILITARY", "AIR NATIONAL GUARD", "ROYAL AUSTRALIAN", "ROYAL NEW ZEALAND",
    "DEFENSE", "GENDARMERIE", "NATO", "GOVERNMENT", "POLICE", "RCMP",
    "SHERIFF", "STATE PATROL", "HIGHWAY PATROL", "LAW ENFORCEMENT", "BORDER PATROL",
    "CUSTOMS"
]

MILITARY_HEX_SET = set()
last_military_db_load = None

def load_military_db():
    global last_military_db_load
    print("🔄 Loading offline military database from wiedehopf/tar1090-db...")
    try:
        url = 'https://github.com/wiedehopf/tar1090-db/raw/csv/aircraft.csv.gz'
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = gzip.decompress(response.content).decode('utf-8').split('\n')
            MILITARY_HEX_SET.clear()  # Clear existing before reloading
            for line in data:
                parts = line.split(';')
                # In this CSV schema, flag strings starting with '1' ('10', '11000') define military
                if len(parts) > 3 and parts[3].startswith('1'):
                    MILITARY_HEX_SET.add(parts[0].upper().strip())
            print(f"✅ Successfully cached {len(MILITARY_HEX_SET)} military hexes for offline detection!")
            last_military_db_load = datetime.now()
    except Exception as e:
        print(f"⚠️ Failed to load military db: {e}")

def get_aircraft_metadata(hex_code):
    """Fetch missing aircraft metadata from HexDB.io."""
    try:
        url = f"https://hexdb.io/api/v1/aircraft/{hex_code}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"⚠️ Error fetching metadata for {hex_code}: {e}")
    return {}

def get_live_flight(hex_code):
    """Fetch the current flight/callsign for a hex from the local ADS-B feed."""
    try:
        data = requests.get(AIRCRAFT_URL, timeout=5).json()
        for ac in data.get("aircraft", []):
            if ac.get("hex", "").upper().strip() == hex_code.upper():
                return (ac.get("flight") or "").strip() or None
    except Exception:
        pass
    return None

def get_external_v2_metadata(hex_code):
    """Fetch supplement metadata from external v2 Hex APIs (ADSB.one, ADSB.fi)."""
    endpoints = [
        f"https://api.adsb.one/v2/hex/{hex_code}",
        f"https://opendata.adsb.fi/api/v2/hex/{hex_code}"
    ]
    for url in endpoints:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("ac") and len(data["ac"]) > 0:
                    return data["ac"][0]  # Return the first matching aircraft object
        except Exception as e:
            print(f"⚠️ Error fetching external data from {url} for {hex_code}: {e}")
    return {}

def get_aircraft_image(hex_code, ac_type=None):
    """Fetch aircraft image URL from Planespotters.net API with ADSB-X silhouette fallback."""
    # 1. Try Planespotters (Real Photo)
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{hex_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("photos"):
                return data["photos"][0]["thumbnail_large"]["src"]
    except Exception:
        pass

    # 2. Fallback to ADSBExchange Silhouette
    if ac_type and ac_type != "Unknown":
        silhouette_url = f"https://globe.adsbexchange.com/aircraft_sil/{ac_type.upper()}.png"
        return silhouette_url
    
    return None

def build_message(hex_code, flight, typ, reg, owner, manufacturer, type_label, alt, gs, db_flags_str, dist, timestamp, is_mil, feed_id):
    """Build the standard notification message."""
    dist_str = f"Distance: {dist:.1f} km\n" if dist else ""
    
    type_str = typ
    parts = []
    if manufacturer != "Unknown" and manufacturer: parts.append(manufacturer)
    if type_label != "Unknown" and type_label: parts.append(type_label)
    if parts:
        type_str = f"{typ} - {' '.join(parts)}"

    reg_str = reg
    if owner != "Unknown" and owner:
        reg_str = f"{reg} - {owner}"

    return f"""{ '🪖 🚨 <b>MILITARY AIRCRAFT!</b>' if is_mil else '🛩️ <b>New Aircraft</b>' }

<b>Hex:</b> <code>{hex_code}</code>
<b>Callsign:</b> {flight}
<b>Type:</b> {type_str}
<b>Reg:</b> {reg_str}
<b>Alt:</b> {alt} ft
<b>Speed:</b> {gs} kts
<b>DB Flags:</b> {db_flags_str}
{dist_str}<i>{timestamp}</i>

🔗 <a href="http://adsbexchange.local/tar1090/?icao={hex_code}">📍 Local Map</a>
🔗 <a href="https://globe.adsbexchange.com/?feed={feed_id}&icao={hex_code}">🌐 Remote Map</a>
🔗 <a href="https://globe.adsbexchange.com/?icao={hex_code}">🌍 Public Globe</a>"""

def send_telegram(chat_id, message, image_url=None, is_mil=False, retries=3):
    label = "🪖 MILITARY" if is_mil else "✈️ CIVIL"
    for attempt in range(retries):
        try:
            if image_url:
                sent = bot.send_photo(chat_id, image_url, caption=message, parse_mode="HTML")
            else:
                try:
                    with open("default.jfif", "rb") as default_img:
                        sent = bot.send_photo(chat_id, default_img, caption=message, parse_mode="HTML")
                except FileNotFoundError:
                    sent = bot.send_message(chat_id, message, parse_mode="HTML")
            print(f"✅ Telegram {label} message SENT successfully")
            return sent
        except Exception as e:
            print(f"⚠️ Telegram {label} attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2)
    print(f"❌ All retries failed — {label} message not sent")
    return None

def retry_unknown_fields(hex_code):
    """
    Background thread: tries up to UPDATE_RETRIES times (UPDATE_INTERVAL apart)
    to fill in Unknown fields, holding the Telegram post until ready.
    """
    for attempt in range(UPDATE_RETRIES):
        entry = seen_aircraft.get(hex_code)
        if not entry: return # tracking lost
        
        # Check if all fields are resolved, if so, break out early to send immediately!
        if entry["flight"] != "Unknown" and entry["typ"] != "Unknown" and entry["reg"] != "N/A" and entry["owner"] != "Unknown" and entry["manufacturer"] != "Unknown" and entry["type_label"] != "Unknown" and entry["image_url"] is not None:
            break

        time.sleep(UPDATE_INTERVAL)
        
        entry = seen_aircraft.get(hex_code)
        if not entry: return # tracking lost
        
        changed = False

        if entry["flight"] == "Unknown":
            live_cs = get_live_flight(hex_code)
            if live_cs:
                entry["flight"] = live_cs
                changed = True
                print(f"   ↩️  {hex_code}: callsign updated → {live_cs}")

        if entry["typ"] == "Unknown" or not entry.get("is_mil", False):
            external_meta = get_external_v2_metadata(hex_code)
            if external_meta:
                if not entry.get("is_mil", False):
                    ext_db_flags = external_meta.get("dbFlags", 0)
                    if ext_db_flags & 1: 
                        entry["is_mil"] = True
                        changed = True
                        print(f"   ↩️  {hex_code}: military flag updated from external API")
                if entry["typ"] == "Unknown":
                    t = external_meta.get("desc") or external_meta.get("t") or "Unknown"
                    if t != "Unknown":
                        entry["typ"] = t
                        changed = True
                        print(f"   ↩️  {hex_code}: type updated from external API → {t}")

        if entry["typ"] == "Unknown" or entry["reg"] == "N/A" or entry["owner"] == "Unknown" or entry["manufacturer"] == "Unknown" or entry["type_label"] == "Unknown":
            meta = get_aircraft_metadata(hex_code)
            if meta:
                if entry["reg"] == "N/A":
                    r = meta.get("Registration", "N/A")
                    if r and r != "N/A": entry["reg"] = r; changed = True; print(f"   ↩️  {hex_code}: reg updated → {r}")
                if entry["typ"] == "Unknown":
                    t = meta.get("ICAOTypeCode") or meta.get("Model") or "Unknown"
                    if t != "Unknown": entry["typ"] = t; changed = True; print(f"   ↩️  {hex_code}: type updated → {t}")
                if entry["owner"] == "Unknown":
                    o = meta.get("RegisteredOwners") or "Unknown"
                    if o != "Unknown": entry["owner"] = o; changed = True; print(f"   ↩️  {hex_code}: owner updated → {o}")
                if entry["manufacturer"] == "Unknown":
                    m = meta.get("Manufacturer") or "Unknown"
                    if m != "Unknown": entry["manufacturer"] = m; changed = True; print(f"   ↩️  {hex_code}: manufacturer updated → {m}")
                if entry["type_label"] == "Unknown":
                    tl = meta.get("Type") or "Unknown"
                    if tl != "Unknown": entry["type_label"] = tl; changed = True; print(f"   ↩️  {hex_code}: type label updated → {tl}")

        if not entry["image_url"]:
            new_image = get_aircraft_image(hex_code, ac_type=entry["typ"])
            if new_image:
                entry["image_url"] = new_image
                entry["new_image_found"] = True
                changed = True
                print(f"   ↩️  {hex_code}: image updated")

        if changed:
            entry["needs_metadata_update"] = True

    # --- END LOOP (Ready to publish!) ---
    entry = seen_aircraft.get(hex_code)
    if not entry: return
    
    # Final 'is_mil' re-evaluation after 30 seconds of checking API and CSVs
    is_mil = entry["is_mil"]
    typ = entry["typ"]
    owner = entry["owner"]
    
    if not is_mil:
        if typ != "Unknown":
            if typ.upper() in MILITARY_AIRCRAFT_TYPES:
                is_mil = True
            elif ("F-16" in typ or "F-35" in typ or "C-130" in typ or "B-52" in typ or "T-38" in typ or "UH-60" in typ or "A-10" in typ):
                is_mil = True
        if not is_mil and owner != "Unknown":
            if any(kw in owner.upper() for kw in MILITARY_OWNER_KEYWORDS):
                is_mil = True
                
    entry["is_mil"] = is_mil
    
    if is_mil and not ENABLE_MILITARY: return
    if not is_mil and not ENABLE_CIVIL: return
    
    # Generate the DB flags string again if is_mil changed
    if is_mil and "Military" not in entry["db_flags_str"]:
        if entry["db_flags_str"] == "None":
            entry["db_flags_str"] = "Military 🪖"
        else:
            entry["db_flags_str"] = "Military 🪖, " + entry["db_flags_str"]

    message = build_message(
        hex_code, entry["flight"], entry["typ"], entry["reg"], entry["owner"],
        entry["manufacturer"], entry["type_label"], entry["alt"], entry["gs"],
        entry["db_flags_str"], entry["dist"], entry["timestamp"], is_mil, FEED_ID
    )
    
    target_chat = MILITARY_CHAT_ID if is_mil else CIVIL_CHAT_ID
    entry["chat_id"] = target_chat
    
    sent = send_telegram(target_chat, message, image_url=entry["image_url"], is_mil=is_mil)
    if sent:
        entry["sent_msg"] = sent
        entry["is_published"] = True
        entry["was_photo"] = (sent.content_type == 'photo')
        print(f"   → Alert for {hex_code} {entry['flight']} ({'MIL' if is_mil else 'CIV'}) [DELAYED]")

def run_cli_test(test_hex):
    print(f"🛠️ Running CLI Test for Hex: {test_hex}")
    test_hex = test_hex.upper().strip()
    load_military_db()
    
    flight, typ, db_flags_val = "Unknown", "Unknown", 0
    is_mil = False
    
    # 1. Check local feed just in case it's literally flying above right now
    try:
        data = requests.get(AIRCRAFT_URL, timeout=5).json()
        for ac in data.get("aircraft", []):
            if ac.get("hex", "").upper().strip() == test_hex:
                flight = (ac.get("flight") or "").strip() or "Unknown"
                typ = ac.get("t") or ac.get("desc") or "Unknown"
                db_flags_val = ac.get("dbFlags", 0)
                is_mil = bool(db_flags_val & 1)
                break
    except:
        pass
        
    if test_hex in MILITARY_HEX_SET:
        is_mil = True

    owner, reg, manufacturer, type_label = "Unknown", "N/A", "Unknown", "Unknown"
    
    # 2. Check external APIs if not explicitly tracked military
    if typ == "Unknown" or not is_mil:
        external_meta = get_external_v2_metadata(test_hex)
        if external_meta:
            if not is_mil and (external_meta.get("dbFlags", 0) & 1):
                is_mil = True
            if typ == "Unknown":
                typ = external_meta.get("desc") or external_meta.get("t") or "Unknown"
                
    # 3. Check HexDB
    meta = get_aircraft_metadata(test_hex)
    if meta:
        reg = meta.get("Registration", "N/A")
        if typ == "Unknown": typ = meta.get("ICAOTypeCode") or meta.get("Model") or "Unknown"
        owner = meta.get("RegisteredOwners") or "Unknown"
        manufacturer = meta.get("Manufacturer") or "Unknown"
        type_label = meta.get("Type") or "Unknown"

    # 4. Final Fallback Logic
    if not is_mil:
        if typ != "Unknown":
            if typ.upper() in MILITARY_AIRCRAFT_TYPES:
                is_mil = True
            elif ("F-16" in typ or "F-35" in typ or "C-130" in typ or "B-52" in typ or "T-38" in typ or "UH-60" in typ or "A-10" in typ):
                is_mil = True
        if not is_mil and owner != "Unknown":
            if any(kw in owner.upper() for kw in MILITARY_OWNER_KEYWORDS):
                is_mil = True

    flags = []
    if is_mil: flags.append("Military 🪖")
    if db_flags_val & 2: flags.append("Interested ⭐")
    if db_flags_val & 4: flags.append("LADD 🔒")
    if db_flags_val & 8: flags.append("PIA 🕵️")
    db_flags_str = ", ".join(flags) if flags else "None"

    # Simulate fake flight physics
    alt, gs, dist = 24500, 310, 42.5
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    image_url = get_aircraft_image(test_hex, ac_type=typ)
    
    message = build_message(
        test_hex, flight, typ, reg, owner, manufacturer, type_label,
        alt, gs, db_flags_str, dist, timestamp, is_mil, FEED_ID
    )
    
    target_chat = MILITARY_CHAT_ID if is_mil else CIVIL_CHAT_ID
    print(f"   → Test alert built for {test_hex} ({'MIL' if is_mil else 'CIV'})")
    send_telegram(target_chat, message, image_url=image_url, is_mil=is_mil)
    sys.exit(0)

# ==================== MAIN INIT ====================
if len(sys.argv) > 2 and sys.argv[1] == "--test":
    run_cli_test(sys.argv[2])

print("🛩️ Multi-Channel ADS-B Telegram Alert started...")
load_military_db()

# Startup messages
if ENABLE_CIVIL:
    send_telegram(CIVIL_CHAT_ID, "🚀 All-Aircraft script restarted (Civil Channel)")
if ENABLE_MILITARY:
    send_telegram(MILITARY_CHAT_ID, "🚀 All-Aircraft script restarted (Military Channel)", is_mil=True)

# Auto-detect location
RECEIVER_LAT = RECEIVER_LAT_FALLBACK
RECEIVER_LON = RECEIVER_LON_FALLBACK

try:
    rec = requests.get(RECEIVER_URL, timeout=5).json()
    if rec.get("lat") is not None and rec.get("lon") is not None:
        RECEIVER_LAT = rec.get("lat")
        RECEIVER_LON = rec.get("lon")
        print(f"✅ Auto-detected location: {RECEIVER_LAT}, {RECEIVER_LON}")
    elif RECEIVER_LAT is not None and RECEIVER_LON is not None:
        print(f"✅ Using fallback location: {RECEIVER_LAT}, {RECEIVER_LON}")
    else:
        print("⚠️ Location not found in receiver.json and no fallback provided.")
except:
    if RECEIVER_LAT is not None and RECEIVER_LON is not None:
        print(f"⚠️ Could not reach receiver.json. Using fallback location: {RECEIVER_LAT}, {RECEIVER_LON}")
    else:
        print("⚠️ Could not reach receiver.json and no fallback provided.")

seen_aircraft = {}

while True:
    try:
        now = datetime.now()
        
        # Weekly automatic refresh of military database
        if last_military_db_load and (now - last_military_db_load).total_seconds() > 7 * 24 * 3600:
            t_reload = threading.Thread(target=load_military_db, daemon=True)
            t_reload.start()

        data = requests.get(AIRCRAFT_URL, timeout=8).json()

        for ac in data.get("aircraft", []):
            hex_code = ac.get("hex", "").upper().strip()
            if not hex_code: continue

            # Initial Military Detection (from local feed + offline DB database cache)
            db_flags_val = ac.get("dbFlags", 0)
            is_mil = bool(db_flags_val & 1) or (hex_code in MILITARY_HEX_SET)

            # NOTE: We can't definitively check toggles yet, because it might secretly be
            # a military plane that the local feed didn't catch the dbFlags for!
            # We defer toggle checking inside the `hex_code not in seen_aircraft` block.

            lat = ac.get("lat")
            lon = ac.get("lon")
            if not lat or not lon: continue

            alt = ac.get("alt_baro")
            if MIN_ALTITUDE_FT is not None and isinstance(alt, (int, float)) and alt < MIN_ALTITUDE_FT: continue

            dist = None
            if RECEIVER_LAT and RECEIVER_LON:
                dist = haversine(RECEIVER_LAT, RECEIVER_LON, lat, lon)
                if MAX_DISTANCE_KM and dist > MAX_DISTANCE_KM: continue

            gs = ac.get("gs", "N/A")

            # --- ALREADY SEEN? Dynamic updating ---
            if hex_code in seen_aircraft:
                entry = seen_aircraft[hex_code]
                entry["last_seen"] = now

                alt_changed = (entry["alt"] != alt)
                gs_changed = (entry["gs"] != gs)
                dist_changed = (entry.get("dist") != dist)

                # Aggressive local feed re-check for Unknown flight/type/reg
                live_flight = (ac.get("flight") or "").strip()
                if entry["flight"] == "Unknown" and live_flight:
                    entry["flight"] = live_flight
                    entry["needs_metadata_update"] = True
                    print(f"   ↩️  {hex_code}: callsign updated from live feed → {live_flight}")

                live_typ = ac.get("t") or ac.get("desc") or ""
                if entry["typ"] == "Unknown" and live_typ and live_typ != "Unknown":
                    entry["typ"] = live_typ
                    entry["needs_metadata_update"] = True
                    print(f"   ↩️  {hex_code}: type updated from live feed → {live_typ}")
                
                live_reg = ac.get("r") or ""
                if entry["reg"] == "N/A" and live_reg and live_reg != "N/A":
                    entry["reg"] = live_reg
                    entry["needs_metadata_update"] = True
                    print(f"   ↩️  {hex_code}: reg updated from live feed → {live_reg}")

                if alt_changed or gs_changed or dist_changed:
                    entry["alt"] = alt
                    entry["gs"] = gs
                    entry["dist"] = dist
                    entry["timestamp"] = now.strftime('%Y-%m-%d %H:%M:%S')

                time_since_edit = (now - entry["last_tg_edit"]).total_seconds()
                has_pending_meta = entry.get("needs_metadata_update", False)
                has_pending_img = entry.get("new_image_found", False)

                if has_pending_meta or has_pending_img or ((alt_changed or gs_changed or dist_changed) and time_since_edit >= 30):
                    if not entry.get("is_published", False):
                        continue # DO NOT edit if we haven't even posted yet!
                    entry["last_tg_edit"] = now
                    entry["needs_metadata_update"] = False
                    
                    new_msg = build_message(
                        hex_code, entry["flight"], entry["typ"], entry["reg"], entry["owner"],
                        entry["manufacturer"], entry["type_label"], entry["alt"], entry["gs"],
                        entry["db_flags_str"], entry["dist"], entry["timestamp"], entry["is_mil"], FEED_ID
                    )
                    
                    msg_obj = entry["sent_msg"]
                    if msg_obj:
                        try:
                            if has_pending_img:
                                bot.edit_message_media(
                                    media=telebot.types.InputMediaPhoto(entry["image_url"], caption=new_msg, parse_mode="HTML"),
                                    chat_id=entry["chat_id"],
                                    message_id=msg_obj.message_id
                                )
                                entry["new_image_found"] = False
                                entry["was_photo"] = True
                            else:
                                if entry["was_photo"]:
                                    bot.edit_message_caption(caption=new_msg, chat_id=entry["chat_id"], message_id=msg_obj.message_id, parse_mode="HTML")
                                else:
                                    bot.edit_message_text(text=new_msg, chat_id=entry["chat_id"], message_id=msg_obj.message_id, parse_mode="HTML")
                        except Exception as e:
                            pass # unchanged or error
                continue

            # --- NEW AIRCRAFT ---
            flight = (ac.get("flight") or "").strip() or "Unknown"
            typ = ac.get("t") or ac.get("desc") or "Unknown"
            reg = ac.get("r") or "N/A"
            owner = "Unknown"
            manufacturer = "Unknown"
            type_label = "Unknown"

            # Decode DB Flags (Initially grab value)
            db_flags_val = ac.get("dbFlags", 0)
            
            # If typ was unknown or NOT explicitly identified as military locally, aggressively check external APIs
            external_meta = {}
            if typ == "Unknown" or not is_mil:
                external_meta = get_external_v2_metadata(hex_code)
                if external_meta:
                    if not is_mil:
                        ext_db_flags = external_meta.get("dbFlags", 0)
                        if ext_db_flags & 1: 
                            is_mil = True
                    if typ == "Unknown":
                        typ = external_meta.get("desc") or external_meta.get("t") or "Unknown"

            # Standard HexDB hook (mostly for Reg/Owner/Manufacturer metadata)
            if typ == "Unknown" or reg == "N/A" or owner == "Unknown" or manufacturer == "Unknown" or type_label == "Unknown":
                meta = get_aircraft_metadata(hex_code)
                if meta:
                    if reg == "N/A": reg = meta.get("Registration", "N/A")
                    if typ == "Unknown": typ = meta.get("ICAOTypeCode") or meta.get("Model") or "Unknown"
                    if owner == "Unknown": owner = meta.get("RegisteredOwners") or "Unknown"
                    if manufacturer == "Unknown": manufacturer = meta.get("Manufacturer") or "Unknown"
                    if type_label == "Unknown": type_label = meta.get("Type") or "Unknown"

            # Build Text DB Flags
            flags = []
            if is_mil: flags.append("Military 🪖")
            if db_flags_val & 2: flags.append("Interested ⭐")
            if db_flags_val & 4: flags.append("LADD 🔒")
            if db_flags_val & 8: flags.append("PIA 🕵️")

            db_flags_str = ", ".join(flags) if flags else "None"
            timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
            image_url = get_aircraft_image(hex_code, ac_type=typ)

            # Track in global state (initially unpublished)
            seen_aircraft[hex_code] = {
                "last_seen": now,
                "last_tg_edit": now,
                "is_published": False,
                "sent_msg": None,
                "chat_id": None,
                "was_photo": False,
                "flight": flight, "typ": typ, "reg": reg, "owner": owner,
                "manufacturer": manufacturer, "type_label": type_label,
                "image_url": image_url,
                "alt": alt, "gs": gs, "dist": dist, "timestamp": timestamp,
                "db_flags_str": db_flags_str, "is_mil": is_mil
            }

            # Check if fields are still missing
            needs_update = (
                flight == "Unknown" or
                typ == "Unknown" or
                reg == "N/A" or
                owner == "Unknown" or
                image_url is None
            )

            if not needs_update:
                # We have all data right away! Evaluate toggles and send immediately.
                if is_mil and not ENABLE_MILITARY: continue
                if not is_mil and not ENABLE_CIVIL: continue

                target_chat = MILITARY_CHAT_ID if is_mil else CIVIL_CHAT_ID
                message = build_message(
                    hex_code, flight, typ, reg, owner, manufacturer, type_label,
                    alt, gs, db_flags_str, dist, timestamp, is_mil, FEED_ID
                )
                sent = send_telegram(target_chat, message, image_url=image_url, is_mil=is_mil)
                if sent:
                    seen_aircraft[hex_code]["sent_msg"] = sent
                    seen_aircraft[hex_code]["is_published"] = True
                    seen_aircraft[hex_code]["chat_id"] = target_chat
                    seen_aircraft[hex_code]["was_photo"] = (sent.content_type == 'photo')
                    print(f"   → Alert for {hex_code} {flight} ({'MIL' if is_mil else 'CIV'}) [INSTANT]")
            else:
                # Missing fields -> Background Delay Thread
                t = threading.Thread(target=retry_unknown_fields, args=(hex_code,), daemon=True)
                t.start()

        # Prune older than 10 minutes
        cutoff = now - timedelta(minutes=10)
        seen_aircraft = {h: data for h, data in seen_aircraft.items() if data["last_seen"] > cutoff}

    except Exception as e:
        print(f"Data fetch error: {e}")

    time.sleep(POLL_INTERVAL)
