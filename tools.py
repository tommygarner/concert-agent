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
from thefuzz import fuzz

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

# Side By Side Shows cache (6hr TTL)
_SBS_CACHE_PATH = Path("data/sbs_cache.json")
_SBS_CACHE_TTL = 3600  # 1 hour

# Do512 cache (1hr TTL)
_DO512_CACHE_PATH = Path("data/do512_cache.json")
_DO512_CACHE_TTL = 3600  # 1 hour

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

def match_artist_to_event(event_name: str, profile: dict, threshold: int = 75):
    """Fuzzy-match an event name against the artist profile. Returns (score, tier) or (0, None)."""
    event_lower = event_name.lower()
    best_score, best_tier, best_match = 0, None, 0
    for artist, info in profile.items():
        # Fast path: exact substring match
        if artist in event_lower:
            return info['score'], info['tier']
        # Fuzzy: compare artist name against event name tokens
        ratio = fuzz.partial_ratio(artist, event_lower)
        if ratio >= threshold and info['score'] > best_match:
            best_score = info['score']
            best_tier = info['tier']
            best_match = info['score']
    return best_score, best_tier


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

def search_concerts(keyword: str = None, city: str = "Austin", genre: str = None, start_date: str = None, end_date: str = None):
    """Search concerts via Ticketmaster ranked by listener affinity. Automatically falls back to
    Do512 when Ticketmaster has no results for a keyword search (catches indie/mid-size acts).
    Optional filters: genre (e.g. 'rock', 'jazz', 'hip-hop'), start_date and end_date (YYYY-MM-DD)."""
    if not TICKETMASTER_API_KEY: return "Missing TM Key."
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {"apikey": TICKETMASTER_API_KEY, "city": city,
              "classificationName": "music", "size": 50, "sort": "date,asc"}
    if keyword: params["keyword"] = keyword
    if genre: params["keyword"] = f"{params.get('keyword', '')} {genre}".strip()
    if start_date: params["startDateTime"] = f"{start_date}T00:00:00Z"
    if end_date: params["endDateTime"] = f"{end_date}T23:59:59Z"
    response = requests.get(url, params=params)
    if response.status_code != 200: return f"Error: {response.status_code}"
    events = response.json().get("_embedded", {}).get("events", [])
    profile = load_artist_profile()
    results = []
    for event in events:
        name = event.get("name")
        v_info = event.get("_embedded", {}).get("venues", [{}])[0]
        score, tier = match_artist_to_event(name, profile)
        price_ranges = event.get("priceRanges", [])
        price_str = ""
        if price_ranges:
            low = price_ranges[0].get("min")
            high = price_ranges[0].get("max")
            currency = price_ranges[0].get("currency", "USD")
            if low and high:
                price_str = f"${low:.0f}-${high:.0f} {currency}"
            elif low:
                price_str = f"From ${low:.0f} {currency}"
        results.append({
            "name": name, "venue": v_info.get("name"), "address": v_info.get("address", {}).get("line1", ""),
            "date": event.get("dates", {}).get("start", {}).get("localDate"),
            "url": event.get("url"), "score": score, "tier": tier, "price": price_str,
            "source": "Ticketmaster",
        })
    results.sort(key=lambda x: (x["score"], x["date"] if x["date"] else ""), reverse=True)

    # If a keyword was given but Ticketmaster found nothing, automatically check Do512
    if keyword and not results:
        kw_lower = keyword.lower()
        try:
            do512_events = _fetch_do512()
        except Exception:
            do512_events = []
        for evt in do512_events:
            evt_name_lower = evt.get("name", "").lower()
            artists_lower = " ".join(evt.get("artists", [])).lower()
            if kw_lower in evt_name_lower or kw_lower in artists_lower or \
               fuzz.token_set_ratio(kw_lower, evt_name_lower) >= 85:
                score, tier = match_artist_to_event(evt["name"], profile)
                results.append({
                    "name": evt["name"],
                    "venue": evt.get("venue", ""),
                    "address": evt.get("venue_address", ""),
                    "date": evt.get("date", ""),
                    "url": evt.get("url", ""),
                    "score": score, "tier": tier,
                    "price": evt.get("ticket_info", ""),
                    "source": "Do512",
                })
        results.sort(key=lambda x: x["date"] if x["date"] else "")

    return results[:10]

def _fetch_side_by_side():
    """Fetch and cache events from sidebysideshows.com. Returns list of event dicts."""
    # Check cache
    if _SBS_CACHE_PATH.exists():
        try:
            cache = json.loads(_SBS_CACHE_PATH.read_text())
            if time.time() - cache.get("cached_at", 0) < _SBS_CACHE_TTL:
                return cache.get("events", [])
        except Exception:
            pass

    try:
        resp = requests.get("https://sidebysideshows.com/", timeout=15)
        if resp.status_code != 200:
            return []

        # Extract initialShows JSON from Next.js __next_f payload
        # Data may be escaped (\" instead of ") inside a script string
        match = re.search(r'initialShows\\?":\s*(\[.*?\])\s*,\s*\\?"initialSelectedDate', resp.text, re.DOTALL)
        if not match:
            return []

        raw = match.group(1)
        # Unescape if the JSON was inside an escaped string
        if '\\"' in raw:
            raw = raw.replace('\\"', '"')
        shows = json.loads(raw)
        events = []
        for show in shows:
            artists = []
            for stage in show.get("stages", []):
                for artist in stage.get("artists", []):
                    artists.append(artist.get("name", ""))

            venue = show.get("venue", {})
            price = show.get("price", "")
            events.append({
                "name": show.get("name", ""),
                "artists": artists,
                "venue": venue.get("name", ""),
                "address": venue.get("street", ""),
                "date": show.get("date", ""),
                "time": show.get("time", ""),
                "price": f"${price}" if price and price != "0.00" else "Free/TBD",
                "url": show.get("tickets_link", f"https://sidebysideshows.com{show.get('path', '')}"),
                "source": "Side By Side Shows",
            })

        # Cache results
        _SBS_CACHE_PATH.parent.mkdir(exist_ok=True)
        _SBS_CACHE_PATH.write_text(json.dumps({"events": events, "cached_at": time.time()}))
        return events
    except Exception:
        return []


def _parse_do512_event(e):
    """Parse a single event dict from the do512.com JSON API."""
    try:
        permalink = e.get("permalink", "")
        date_str = e.get("begin_date", "")
        if not date_str:
            m = re.search(r'/events/(\d{4})/(\d+)/(\d+)/', permalink)
            if m:
                y, mo, d = m.groups()
                date_str = f"{y}-{int(mo):02d}-{int(d):02d}"

        venue = e.get("venue") or {}
        buy_url = e.get("buy_url", "")
        if not buy_url:
            buy_url = f"https://do512.com{permalink}"

        artists = [a.get("title", "") for a in (e.get("artists") or []) if a.get("title")]

        imagery = e.get("imagery") or {}
        aws = imagery.get("aws") or {}
        image_url = aws.get("poster_w_400") or aws.get("poster_w_800") or aws.get("cover_image_h_300_w_864") or ""

        return {
            "name": e.get("title", ""),
            "venue": venue.get("title", "") if isinstance(venue, dict) else "",
            "venue_address": venue.get("full_address", "") if isinstance(venue, dict) else "",
            "date": date_str,
            "time": e.get("begin_time", ""),
            "ticket_info": e.get("ticket_info", ""),
            "category": e.get("category", ""),
            "artists": artists,
            "image_url": image_url,
            "url": buy_url,
            "do512_url": f"https://do512.com{permalink}",
            "is_free": e.get("is_free", False),
            "sold_out": e.get("sold_out", False),
            "do512_id": e.get("id"),
            "source": "Do512",
        }
    except Exception:
        return None


def _fetch_do512_day(session, date_str):
    """Fetch all events for a specific date from the do512.com JSON API. Returns list of event dicts."""
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return []

    base_url = f"https://do512.com/events/{dt.year}/{dt.month}/{dt.day}.json"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    events = []
    page = 1
    while True:
        try:
            params = {} if page == 1 else {"page": page}
            resp = session.get(base_url, params=params, headers=headers, timeout=12)
            if resp.status_code != 200:
                break
            data = resp.json()
            for e in data.get("events", []):
                parsed = _parse_do512_event(e)
                if parsed:
                    events.append(parsed)
            paging = data.get("paging", {})
            if page >= paging.get("total_pages", 1):
                break
            page += 1
        except Exception:
            break
    return events


def _fetch_do512():
    """Fetch and cache upcoming music events from do512.com.
    Uses the JSON API to cover today through 90 days ahead (parallel day requests).
    Returns list of event dicts."""
    if _DO512_CACHE_PATH.exists():
        try:
            cache = json.loads(_DO512_CACHE_PATH.read_text())
            if time.time() - cache.get("cached_at", 0) < _DO512_CACHE_TTL:
                return cache.get("events", [])
        except Exception:
            pass

    from datetime import datetime, timedelta
    from concurrent.futures import ThreadPoolExecutor, as_completed

    today = datetime.today()
    dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(90)]

    session = requests.Session()
    seen_ids = set()
    events = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_do512_day, session, d): d for d in dates}
        for future in as_completed(futures):
            try:
                for evt in future.result():
                    key = (evt["name"], evt["date"])
                    if key not in seen_ids:
                        seen_ids.add(key)
                        events.append(evt)
            except Exception:
                pass

    events.sort(key=lambda x: x.get("date", ""))

    try:
        if events:
            _DO512_CACHE_PATH.parent.mkdir(exist_ok=True)
            _DO512_CACHE_PATH.write_text(json.dumps({"events": events, "cached_at": time.time()}))
    except Exception:
        pass  # Cache write failure is non-fatal; return events anyway
    return events


def search_do512():
    """
    Browse all upcoming Austin music events from do512.com (covers indie, mid-size, and major acts
    that may not appear on Ticketmaster). Returns events ranked by your listening history.
    Use this when searching for a specific artist or when Ticketmaster returns no results.
    """
    profile = load_artist_profile()
    events = _fetch_do512()
    if not events:
        return "Do512 is currently unavailable."

    scored = []
    for evt in events:
        # Match against event name + all artists listed
        search_str = evt["name"] + " " + " ".join(evt.get("artists", []))
        score, tier = match_artist_to_event(search_str, profile)
        scored.append((score, tier, evt))
    scored.sort(key=lambda x: x[0], reverse=True)

    lines = []
    for score, tier, evt in scored:
        tier_tag = f" [{tier.upper()}]" if tier else ""
        price = evt.get("ticket_info", "")
        price_str = f" [{price}]" if price else ""
        lines.append(f"{evt['date']}: {evt['name']} @ {evt['venue']}{price_str} — {evt['url']}{tier_tag}")

    matched = [l for l in lines if any(t in l for t in ["[SUPERFAN]", "[FAN]", "[CASUAL]"])]
    unmatched = [l for l in lines if l not in matched]

    result = []
    if matched:
        result.append(f"=== Matched to your profile ({len(matched)} shows) ===")
        result.extend(matched)
    result.append(f"\n=== All Do512 shows ({len(unmatched)} more) ===")
    result.extend(unmatched[:40])
    return "\n".join(result)


def search_small_venue_calendar(venue_name: str):
    """
    Search indie/small venue shows from Showlist Austin AND Side By Side Shows.
    If venue_name is provided, filters results to that venue.
    Falls back to Ticketmaster if both indie sources are unavailable.
    Example: search_small_venue_calendar("Mohawk")
    """
    results = []
    vl = venue_name.lower()

    # Source 1: Showlist Austin
    showlist_result = _scrape_showlist(venue_name)

    # Source 2: Side By Side Shows
    sbs_events = _fetch_side_by_side()
    sbs_lines = []
    if sbs_events:
        for evt in sbs_events:
            if vl in evt["venue"].lower() or vl in evt["name"].lower():
                artists_str = ", ".join(evt["artists"][:5]) if evt["artists"] else evt["name"]
                sbs_lines.append(f"{evt['date']}: {artists_str} @ {evt['venue']} [{evt['price']}]")

    # Source 3: Do512
    do512_events = _fetch_do512()
    do512_lines = []
    if do512_events:
        for evt in do512_events:
            if vl in evt.get("venue", "").lower() or vl in evt["name"].lower():
                price = evt.get("ticket_info", "")
                price_str = f" [{price}]" if price else ""
                do512_lines.append(f"{evt['date']}: {evt['name']} @ {evt['venue']}{price_str} — {evt['url']}")

    # Merge results
    if showlist_result and not showlist_result.startswith("No upcoming"):
        results.append("=== Showlist Austin ===")
        results.append(showlist_result)
    if sbs_lines:
        results.append("=== Side By Side Shows ===")
        results.extend(sbs_lines[:15])
    if do512_lines:
        results.append("=== Do512 ===")
        results.extend(do512_lines[:15])

    if results:
        return "\n".join(results)

    # Fallback: search Ticketmaster for venue-specific events
    if TICKETMASTER_API_KEY:
        try:
            resp = requests.get(
                "https://app.ticketmaster.com/discovery/v2/events.json",
                params={"apikey": TICKETMASTER_API_KEY, "keyword": venue_name,
                        "city": "Austin", "classificationName": "music",
                        "size": 10, "sort": "date,asc"},
                timeout=10,
            )
            if resp.status_code == 200:
                events = resp.json().get("_embedded", {}).get("events", [])
                if events:
                    lines = [f"(via Ticketmaster, indie sources unavailable)"]
                    for e in events[:10]:
                        date = e.get("dates", {}).get("start", {}).get("localDate", "TBD")
                        lines.append(f"{date}: {e['name']} @ {e.get('_embedded', {}).get('venues', [{}])[0].get('name', '')}")
                    return "\n".join(lines)
        except Exception:
            pass

    return f"No upcoming shows found for '{venue_name}'."


def search_side_by_side():
    """
    Browse all upcoming indie/niche shows from Side By Side Shows (sidebysideshows.com).
    Returns all Austin events with artist names, venues, dates, and prices.
    Use this to discover niche artists and small venue shows beyond Ticketmaster.
    """
    profile = load_artist_profile()
    events = _fetch_side_by_side()
    if not events:
        return "Side By Side Shows is currently unavailable. Try search_small_venue_calendar instead."

    lines = []
    for evt in events:
        all_names = " ".join(evt["artists"]) if evt["artists"] else evt["name"]
        score, tier = match_artist_to_event(all_names, profile)
        tier_tag = f" [{tier.upper()}]" if tier else ""
        artists_str = ", ".join(evt["artists"][:5]) if evt["artists"] else evt["name"]
        lines.append(f"{evt['date']}: {artists_str} @ {evt['venue']} [{evt['price']}]{tier_tag}")

    if not lines:
        return "No upcoming events found on Side By Side Shows."

    matched = [l for l in lines if any(t in l for t in ["[SUPERFAN]", "[FAN]", "[CASUAL]"])]
    unmatched = [l for l in lines if l not in matched]

    result = []
    if matched:
        result.append(f"=== Matched to your profile ({len(matched)} shows) ===")
        result.extend(matched)
    result.append(f"\n=== All indie shows ({len(unmatched)} more) ===")
    result.extend(unmatched[:30])
    return "\n".join(result)


def _scrape_showlist(venue_name: str):
    """Scrape showlistaustin.com. Returns result string or None on failure."""
    try:
        response = requests.get("http://www.showlistaustin.com/", timeout=10)
        if response.status_code != 200:
            return None

        text = response.text
        if len(text) < 100:  # health check: page loaded but empty/broken
            return None

        lines = text.split('\n')
        matches = []
        current_date = "Upcoming"

        for line in lines:
            if any(day in line for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]):
                current_date = line.strip()

            if venue_name.lower() in line.lower():
                matches.append(f"{current_date}: {line.strip()}")

        if not matches:
            return f"No upcoming shows found for '{venue_name}' on Showlist Austin."

        return "\n".join(matches[:15])
    except Exception:
        return None

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


_PRESALE_CACHE_PATH = Path("data/presale_cache.json")
_PRESALE_CACHE_TTL = 3600  # 1 hour


def get_presale_alerts(city: str = "Austin"):
    """
    Check for upcoming presale windows for the user's top artists.
    Returns events with active or upcoming presales, including presale codes and timing.
    Results cached for 1 hour. Example: get_presale_alerts("Austin")
    """
    # Check cache
    if _PRESALE_CACHE_PATH.exists():
        try:
            cached = json.loads(_PRESALE_CACHE_PATH.read_text())
            if time.time() - cached.get("cached_at", 0) < _PRESALE_CACHE_TTL and cached.get("city") == city:
                return cached["result"]
        except Exception:
            pass

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
        result = "No upcoming presales found for your superfan artists in the next week."
    else:
        lines = []
        for a in alerts:
            lines.append(f"[{a['presale_status']}] {a['event']} at {a['venue']} ({a['event_date']}) - {a['presale_name']} | {a['url']}")
        result = "\n".join(lines)

    # Cache result
    try:
        _PRESALE_CACHE_PATH.parent.mkdir(exist_ok=True)
        _PRESALE_CACHE_PATH.write_text(json.dumps({"result": result, "city": city, "cached_at": time.time()}))
    except Exception:
        pass
    return result


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
    return f"No insider info for '{venue_name}'. You can add it with add_venue_details."


def add_venue_details(name: str, parking: str, age_limit: str, vibe: str, tips: str):
    """Add a new venue to the local knowledge base.
    Example: add_venue_details("Parish", "Street parking on E 6th", "18+", "Mid-size indie rock room", "Balcony has best views")
    """
    venue_path = Path("data/venue_knowledge.json")
    if venue_path.exists():
        with open(venue_path, 'r') as f:
            knowledge = json.load(f)
    else:
        knowledge = []

    # Check for duplicates
    def clean(s): return "".join(filter(str.isalnum, s.lower()))
    for existing in knowledge:
        if clean(name) == clean(existing['name']):
            return f"'{name}' already exists in the knowledge base."

    knowledge.append({
        "name": name,
        "parking": parking,
        "age_limit": age_limit,
        "vibe": vibe,
        "tips": tips,
    })
    venue_path.parent.mkdir(exist_ok=True)
    with open(venue_path, 'w') as f:
        json.dump(knowledge, f, indent=2)
    return f"Added '{name}' to venue knowledge base ({len(knowledge)} venues total)."
