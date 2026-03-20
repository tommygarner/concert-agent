import os
import json
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
