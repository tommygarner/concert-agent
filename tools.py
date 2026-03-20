import os
import json
import time
import requests
import googlemaps
import re
from twilio.rest import Client
from dotenv import load_dotenv
from pathlib import Path
from bs4 import BeautifulSoup

load_dotenv()

# Config
TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY")
GMAPS_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
HOME_ADDRESS = os.getenv("HOME_ADDRESS", "Austin, TX")
PROFILE_PATH = Path("data/artist_profile.json")
SETLISTFM_API_KEY = os.getenv("SETLISTFM_API_KEY", "")

# Simple file-based cache for setlist.fm (24hr TTL, stays under 1440/day limit)
_SETLIST_CACHE_PATH = Path("data/setlist_cache.json")
_SETLIST_CACHE_TTL = 86400  # 24 hours in seconds

def _load_setlist_cache():
    if _SETLIST_CACHE_PATH.exists():
        try:
            return json.loads(_SETLIST_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}

def _save_setlist_cache(cache):
    _SETLIST_CACHE_PATH.parent.mkdir(exist_ok=True)
    _SETLIST_CACHE_PATH.write_text(json.dumps(cache))

def load_artist_profile():
    """Returns {artist_name_lower: {'score': float, 'tier': str}}"""
    if not PROFILE_PATH.exists():
        return {}
    with open(PROFILE_PATH, 'r') as f:
        data = json.load(f)
    return {
        item['artist'].lower(): {
            'score': item['weighted_score'],
            'tier': item.get('tier', 'fan')
        }
        for item in data
    }

def search_concerts(keyword: str = None, city: str = "Austin"):
    """Search major concerts via Ticketmaster, ranked by listener affinity tier."""
    if not TICKETMASTER_API_KEY: return "Missing TM Key."
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {"apikey": TICKETMASTER_API_KEY, "city": city, "classificationName": "music", "size": 50, "sort": "date,asc"}
    if keyword: params["keyword"] = keyword
    response = requests.get(url, params=params)
    if response.status_code != 200: return f"Error: {response.status_code}"
    events = response.json().get("_embedded", {}).get("events", [])
    profile = load_artist_profile()
    results = []
    for event in events:
        name = event.get("name")
        v_info = event.get("_embedded", {}).get("venues", [{}])[0]
        score = 0
        tier = None
        for artist, info in profile.items():
            if artist in name.lower():
                score = info['score']
                tier = info['tier']
                break
        results.append({
            "name": name, "venue": v_info.get("name"), "address": v_info.get("address", {}).get("line1", ""),
            "date": event.get("dates", {}).get("start", {}).get("localDate"),
            "url": event.get("url"), "score": score, "tier": tier
        })
    results.sort(key=lambda x: (x["score"], x["date"] if x["date"] else ""), reverse=True)
    return results[:10]

def search_small_venue_calendar(venue_name: str):
    """
    Search showlistaustin.com for upcoming shows at a specific small venue.
    Example: search_small_venue_calendar("Mohawk")
    """
    try:
        response = requests.get("http://www.showlistaustin.com/", timeout=10)
        if response.status_code != 200: return "Could not reach Showlist Austin."
        
        # Showlist is plain text / simple HTML
        text = response.text
        
        # Find all blocks of text that mention the venue
        # Showlist format is usually: Date \n Artist @ Venue
        lines = text.split('\n')
        matches = []
        current_date = "Upcoming"
        
        for line in lines:
            # Check for date lines (usually starts with a day of the week)
            if any(day in line for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]):
                current_date = line.strip()
            
            if venue_name.lower() in line.lower():
                matches.append(f"{current_date}: {line.strip()}")
        
        if not matches:
            return f"No upcoming shows found for '{venue_name}' on Showlist Austin."
        
        return "\n".join(matches[:15])
    except Exception as e:
        return f"Error searching small venues: {str(e)}"

def get_distance_to_venue(venue_address: str):
    """Calculate driving time/distance."""
    current_home = os.getenv("HOME_ADDRESS", "Austin, TX")
    if not GMAPS_KEY: return "Missing GMaps Key."
    try:
        gmaps = googlemaps.Client(key=GMAPS_KEY)
        result = gmaps.distance_matrix(current_home, venue_address, mode="driving")
        if result['status'] == 'OK':
            element = result['rows'][0]['elements'][0]
            if element['status'] == 'OK':
                return f"Distance: {element['distance']['text']}, Travel Time: {element['duration']['text']} from your home."
        return "Could not calculate distance."
    except Exception as e: return f"Error: {str(e)}"

def send_concert_sms(message: str):
    """Send an SMS alert."""
    sid, token = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    phone, my_phone = os.getenv("TWILIO_PHONE_NUMBER"), os.getenv("MY_PHONE_NUMBER")
    if not sid: return "Twilio not configured."
    try:
        client = Client(sid, token)
        msg = client.messages.create(body=f"🎸 Concert Agent: {message}", from_=phone, to=my_phone)
        return f"SMS sent! SID: {msg.sid}"
    except Exception as e: return f"Error: {str(e)}"

def get_recent_setlist(artist_name: str):
    """
    Fetch the most recent setlist for an artist from setlist.fm.
    Returns a summary of the last show: date, venue, city, and songs played.
    Results are cached for 24 hours to stay within the 1440/day API limit.
    Example: get_recent_setlist("Khruangbin")
    """
    cache_key = artist_name.lower().strip()
    cache = _load_setlist_cache()

    # Return cached result if still fresh
    if cache_key in cache:
        entry = cache[cache_key]
        if time.time() - entry["cached_at"] < _SETLIST_CACHE_TTL:
            return entry["result"]

    headers = {
        "x-api-key": SETLISTFM_API_KEY,
        "Accept": "application/json",
    }
    try:
        resp = requests.get(
            "https://api.setlist.fm/rest/1.0/search/setlists",
            params={"artistName": artist_name, "p": 1},
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return f"Could not fetch setlist for '{artist_name}' (status {resp.status_code})."

        setlists = resp.json().get("setlist", [])
        # Find the most recent setlist that actually has songs
        for sl in setlists:
            songs = []
            for section in sl.get("sets", {}).get("set", []):
                for song in section.get("song", []):
                    if song.get("name"):
                        songs.append(song["name"])
            if not songs:
                continue

            event_date = sl.get("eventDate", "Unknown date")
            venue = sl.get("venue", {})
            venue_name_str = venue.get("name", "Unknown venue")
            city = venue.get("city", {}).get("name", "")
            country = venue.get("city", {}).get("country", {}).get("name", "")

            song_list = ", ".join(songs[:12])
            suffix = f" (+{len(songs) - 12} more)" if len(songs) > 12 else ""
            result = (
                f"{artist_name} — Last show: {event_date} at {venue_name_str}, {city}, {country}. "
                f"Set ({len(songs)} songs): {song_list}{suffix}."
            )
            break
        else:
            result = f"No setlists with song data found for '{artist_name}'."

        # Cache and return
        cache[cache_key] = {"result": result, "cached_at": time.time()}
        _save_setlist_cache(cache)
        return result

    except Exception as e:
        return f"Error fetching setlist: {str(e)}"


def make_gcal_url(event_name: str, date: str, venue: str = "", ticket_url: str = ""):
    """
    Generate a Google Calendar 'Add Event' URL. When a user clicks this link,
    it opens Google Calendar pre-filled with the event details and lets them
    save it and invite friends.
    Example: make_gcal_url("Khruangbin", "2026-04-15", "Stubb's", "https://ticketmaster.com/...")
    """
    from urllib.parse import quote
    try:
        # Parse date and create an all-day event
        from datetime import datetime, timedelta
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_str = dt.strftime("%Y%m%d")
        end_str = (dt + timedelta(days=1)).strftime("%Y%m%d")
    except (ValueError, TypeError):
        date_str = ""
        end_str = ""

    title = quote(event_name)
    location = quote(venue)
    details = quote(f"Tickets: {ticket_url}" if ticket_url else "")

    url = (
        f"https://calendar.google.com/calendar/r/eventedit"
        f"?text={title}"
        f"&dates={date_str}/{end_str}"
        f"&location={location}"
        f"&details={details}"
    )
    return url


def get_presale_alerts(city: str = "Austin"):
    """
    Check for upcoming presale windows for the user's top artists.
    Returns events with active or upcoming presales, including presale codes and timing.
    Example: get_presale_alerts("Austin")
    """
    if not TICKETMASTER_API_KEY: return "Missing TM Key."
    profile = load_artist_profile()
    if not profile: return "No artist profile loaded."

    superfans = [name for name, info in profile.items() if info.get('tier') == 'superfan']
    if not superfans: return "No superfan artists found in your profile."

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    alerts = []

    for artist in superfans[:10]:  # limit API calls
        try:
            resp = requests.get(
                "https://app.ticketmaster.com/discovery/v2/events.json",
                params={"apikey": TICKETMASTER_API_KEY, "keyword": artist, "city": city,
                        "classificationName": "music", "size": 5, "sort": "date,asc"},
                timeout=10,
            )
            if resp.status_code != 200: continue
            events = resp.json().get("_embedded", {}).get("events", [])
            for event in events:
                sales = event.get("sales", {})
                presales = sales.get("presales", [])
                public_sale = sales.get("public", {})

                for ps in presales:
                    start = ps.get("startDateTime", "")
                    end = ps.get("endDateTime", "")
                    ps_name = ps.get("name", "Presale")
                    try:
                        ps_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        ps_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        continue

                    # Include if presale is active now or starts within 7 days
                    if ps_start <= now <= ps_end:
                        status = "ACTIVE NOW"
                    elif now < ps_start and (ps_start - now).days <= 7:
                        status = f"Starts {ps_start.strftime('%b %d %I:%M %p')} UTC"
                    else:
                        continue

                    venue_name = event.get("_embedded", {}).get("venues", [{}])[0].get("name", "")
                    alerts.append({
                        "artist": artist.title(),
                        "event": event.get("name", ""),
                        "venue": venue_name,
                        "event_date": event.get("dates", {}).get("start", {}).get("localDate", ""),
                        "presale_name": ps_name,
                        "presale_status": status,
                        "url": event.get("url", ""),
                    })

                # Also flag if public on-sale is upcoming
                pub_start = public_sale.get("startDateTime", "")
                if pub_start:
                    try:
                        pub_dt = datetime.fromisoformat(pub_start.replace("Z", "+00:00"))
                        if now < pub_dt and (pub_dt - now).days <= 3:
                            venue_name = event.get("_embedded", {}).get("venues", [{}])[0].get("name", "")
                            alerts.append({
                                "artist": artist.title(),
                                "event": event.get("name", ""),
                                "venue": venue_name,
                                "event_date": event.get("dates", {}).get("start", {}).get("localDate", ""),
                                "presale_name": "Public On-Sale",
                                "presale_status": f"Starts {pub_dt.strftime('%b %d %I:%M %p')} UTC",
                                "url": event.get("url", ""),
                            })
                    except (ValueError, AttributeError):
                        pass
        except Exception:
            continue

    if not alerts:
        return "No upcoming presales found for your superfan artists in the next week."

    lines = []
    for a in alerts:
        lines.append(f"[{a['presale_status']}] {a['event']} at {a['venue']} ({a['event_date']}) - {a['presale_name']} | {a['url']}")
    return "\n".join(lines)


def get_venue_details(venue_name: str):
    """Look up insider details from local knowledge base."""
    venue_path = Path("data/venue_knowledge.json")
    if not venue_path.exists(): return "Knowledge base not found."
    with open(venue_path, 'r') as f: knowledge = json.load(f)
    def clean(s): return "".join(filter(str.isalnum, s.lower()))
    target = clean(venue_name)
    for venue in knowledge:
        if target in clean(venue['name']) or clean(venue['name']) in target:
            return f"--- {venue['name']} Info ---\n🅿️ Parking: {venue['parking']}\n🔞 Age: {venue['age_limit']}\n🎸 Vibe: {venue['vibe']}\n💡 Tips: {venue['tips']}"
    return f"No insider info for '{venue_name}'."
