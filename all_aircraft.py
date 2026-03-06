import telebot
import requests
import time
import math
import threading
from datetime import datetime, timedelta

# ============== CONFIG ==============
AIRCRAFT_URL = "http://adsbexchange.local/tar1090/data/aircraft.json"
RECEIVER_URL = "http://adsbexchange.local/tar1090/data/receiver.json"
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# Channels
CIVIL_CHAT_ID = "YOUR_CIVIL_CHAT_ID"    # Channel for all non-military aircraft
MILITARY_CHAT_ID = "YOUR_MILITARY_CHAT_ID" # Channel for military ONLY

# Toggles
ENABLE_CIVIL = True
ENABLE_MILITARY = True

FEED_ID = "YOUR_FEED_ID"          # ← your personal ADS-B Exchange feed ID
POLL_INTERVAL = 18

# FILTERS (change if you want less spam)
MIN_ALTITUDE_FT = None      # e.g. 1500
MAX_DISTANCE_KM = None      # e.g. 80

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

def retry_unknown_fields(hex_code, chat_id, sent_msg,
                          flight, typ, reg, owner, manufacturer, type_label, image_url,
                          alt, gs, db_flags_str, dist, timestamp, is_mil):
    """
    Background thread: tries up to UPDATE_RETRIES times (UPDATE_INTERVAL apart)
    to fill in Unknown callsign / type / reg / image, then edits the Telegram message.
    """
    updated_flight = flight
    updated_typ    = typ
    updated_reg    = reg
    updated_owner  = owner
    updated_manufacturer = manufacturer
    updated_type_label = type_label
    updated_image  = image_url
    
    current_msg_id = sent_msg.message_id
    was_photo = (sent_msg.content_type == 'photo')

    for attempt in range(UPDATE_RETRIES):
        time.sleep(UPDATE_INTERVAL)

        changed = False

        # --- Refresh from live feed (callsign) ---
        if updated_flight == "Unknown":
            live_cs = get_live_flight(hex_code)
            if live_cs:
                updated_flight = live_cs
                changed = True
                print(f"   ↩️  {hex_code}: callsign updated → {updated_flight}")

        # --- Refresh from HexDB (type / reg) ---
        if updated_typ == "Unknown" or updated_reg == "N/A" or updated_owner == "Unknown" or updated_manufacturer == "Unknown" or updated_type_label == "Unknown":
            meta = get_aircraft_metadata(hex_code)
            if meta:
                if updated_reg == "N/A":
                    new_reg = meta.get("Registration", "N/A")
                    if new_reg and new_reg != "N/A":
                        updated_reg = new_reg
                        changed = True
                        print(f"   ↩️  {hex_code}: reg updated → {updated_reg}")
                if updated_typ == "Unknown":
                    new_typ = meta.get("ICAOTypeCode") or meta.get("Model") or "Unknown"
                    if new_typ != "Unknown":
                        updated_typ = new_typ
                        changed = True
                        print(f"   ↩️  {hex_code}: type updated → {updated_typ}")
                if updated_owner == "Unknown":
                    new_owner = meta.get("RegisteredOwners") or "Unknown"
                    if new_owner != "Unknown":
                        updated_owner = new_owner
                        changed = True
                        print(f"   ↩️  {hex_code}: owner updated → {updated_owner}")
                if updated_manufacturer == "Unknown":
                    new_man = meta.get("Manufacturer") or "Unknown"
                    if new_man != "Unknown":
                        updated_manufacturer = new_man
                        changed = True
                        print(f"   ↩️  {hex_code}: manufacturer updated → {updated_manufacturer}")
                if updated_type_label == "Unknown":
                    new_tl = meta.get("Type") or "Unknown"
                    if new_tl != "Unknown":
                        updated_type_label = new_tl
                        changed = True
                        print(f"   ↩️  {hex_code}: type label updated → {updated_type_label}")

        # --- Refresh image if missing ---
        if not updated_image:
            new_image = get_aircraft_image(hex_code, ac_type=updated_typ)
            if new_image:
                updated_image = new_image
                changed = True
                print(f"   ↩️  {hex_code}: image updated")

        if changed:
            new_msg = build_message(
                hex_code, updated_flight, updated_typ, updated_reg, updated_owner, updated_manufacturer, updated_type_label,
                alt, gs, db_flags_str, dist, timestamp, is_mil, FEED_ID
            )
            
            try:
                # If we have a newly discovered image url, we seamlessly update the media
                if updated_image and updated_image != image_url:
                    bot.edit_message_media(
                        media=telebot.types.InputMediaPhoto(updated_image, caption=new_msg, parse_mode="HTML"),
                        chat_id=chat_id,
                        message_id=current_msg_id
                    )
                    image_url = updated_image # So we don't update media again on future iterations
                    was_photo = True
                else:
                    if was_photo:
                        bot.edit_message_caption(
                            caption=new_msg,
                            chat_id=chat_id,
                            message_id=current_msg_id,
                            parse_mode="HTML"
                        )
                    else:
                        bot.edit_message_text(
                            text=new_msg,
                            chat_id=chat_id,
                            message_id=current_msg_id,
                            parse_mode="HTML"
                        )
            except Exception as e:
                print(f"⚠️ Could not update message {current_msg_id}: {e}")

        # Stop retrying once all fields are resolved
        if updated_flight != "Unknown" and updated_typ != "Unknown" and updated_reg != "N/A" and updated_owner != "Unknown" and updated_manufacturer != "Unknown" and updated_type_label != "Unknown" and updated_image is not None:
            print(f"   ✅ {hex_code}: all fields resolved after attempt {attempt+1}")
            break


print("🛩️ Multi-Channel ADS-B Telegram Alert started...")

# Startup messages
if ENABLE_CIVIL:
    send_telegram(CIVIL_CHAT_ID, "🚀 All-Aircraft script restarted (Civil Channel)")
if ENABLE_MILITARY:
    send_telegram(MILITARY_CHAT_ID, "🚀 All-Aircraft script restarted (Military Channel)", is_mil=True)

# Auto-detect location
try:
    rec = requests.get(RECEIVER_URL, timeout=5).json()
    RECEIVER_LAT = rec.get("lat")
    RECEIVER_LON = rec.get("lon")
    print(f"✅ Auto-detected location: {RECEIVER_LAT}, {RECEIVER_LON}")
except:
    print("⚠️ Could not auto-detect location")
    RECEIVER_LAT = RECEIVER_LON = None

seen_aircraft = {}

while True:
    try:
        data = requests.get(AIRCRAFT_URL, timeout=8).json()
        now = datetime.now()

        for ac in data.get("aircraft", []):
            hex_code = ac.get("hex", "").upper().strip()
            if not hex_code: continue

            # Military Detection
            is_mil = bool(ac.get("dbFlags", 0) & 1)

            # Check Toggles
            if is_mil and not ENABLE_MILITARY: continue
            if not is_mil and not ENABLE_CIVIL: continue

            lat = ac.get("lat")
            lon = ac.get("lon")
            if not lat or not lon: continue

            alt = ac.get("alt_baro")
            if MIN_ALTITUDE_FT is not None and isinstance(alt, (int, float)) and alt < MIN_ALTITUDE_FT: continue

            dist = None
            if RECEIVER_LAT and RECEIVER_LON:
                dist = haversine(RECEIVER_LAT, RECEIVER_LON, lat, lon)
                if MAX_DISTANCE_KM and dist > MAX_DISTANCE_KM: continue

            if hex_code not in seen_aircraft:
                flight = (ac.get("flight") or "").strip() or "Unknown"
                typ = ac.get("desc") or ac.get("t") or "Unknown"
                reg = ac.get("r") or "N/A"
                owner = "Unknown"
                manufacturer = "Unknown"
                type_label = "Unknown"
                gs = ac.get("gs", "N/A")

                # Metadata supplement
                if typ == "Unknown" or reg == "N/A" or owner == "Unknown" or manufacturer == "Unknown" or type_label == "Unknown":
                    meta = get_aircraft_metadata(hex_code)
                    if meta:
                        if reg == "N/A": reg = meta.get("Registration", "N/A")
                        if typ == "Unknown": typ = meta.get("ICAOTypeCode") or meta.get("Model") or "Unknown"
                        if owner == "Unknown": owner = meta.get("RegisteredOwners") or "Unknown"
                        if manufacturer == "Unknown": manufacturer = meta.get("Manufacturer") or "Unknown"
                        if type_label == "Unknown": type_label = meta.get("Type") or "Unknown"

                # Decode DB Flags
                db_flags_val = ac.get("dbFlags", 0)
                flags = []
                if db_flags_val & 1: flags.append("Military 🪖")
                if db_flags_val & 2: flags.append("Interested ⭐")
                if db_flags_val & 4: flags.append("LADD 🔒")
                if db_flags_val & 8: flags.append("PIA 🕵️")

                db_flags_str = ", ".join(flags) if flags else "None"
                timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

                message = build_message(
                    hex_code, flight, typ, reg, owner, manufacturer, type_label,
                    alt, gs, db_flags_str, dist, timestamp, is_mil, FEED_ID
                )

                image_url = get_aircraft_image(hex_code, ac_type=typ)
                target_chat = MILITARY_CHAT_ID if is_mil else CIVIL_CHAT_ID

                sent = send_telegram(target_chat, message, image_url=image_url, is_mil=is_mil)
                print(f"   → Alert for {hex_code} {flight} ({'MIL' if is_mil else 'CIV'})")

                # If any field or image is missing, kick off a background retry thread
                needs_update = (
                    flight == "Unknown" or
                    typ == "Unknown" or
                    reg == "N/A" or
                    owner == "Unknown" or
                    manufacturer == "Unknown" or
                    type_label == "Unknown" or
                    image_url is None
                )
                if needs_update and sent is not None:
                    t = threading.Thread(
                        target=retry_unknown_fields,
                        args=(
                            hex_code, target_chat, sent,
                            flight, typ, reg, owner, manufacturer, type_label, image_url,
                            alt, gs, db_flags_str, dist, timestamp, is_mil
                        ),
                        daemon=True
                    )
                    t.start()

            seen_aircraft[hex_code] = now

        cutoff = now - timedelta(minutes=80)
        seen_aircraft = {h: t for h, t in seen_aircraft.items() if t > cutoff}

    except Exception as e:
        print(f"Data fetch error: {e}")

    time.sleep(POLL_INTERVAL)
