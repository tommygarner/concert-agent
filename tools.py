import os
import json
import requests
import googlemaps
from twilio.rest import Client
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Config
TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY")
GMAPS_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
HOME_ADDRESS = os.getenv("HOME_ADDRESS", "Austin, TX")
PROFILE_PATH = Path("data/artist_profile.json")

# Twilio Config
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")
MY_PHONE = os.getenv("MY_PHONE_NUMBER")

def load_artist_profile():
    if not PROFILE_PATH.exists():
        return {}
    with open(PROFILE_PATH, 'r') as f:
        data = json.load(f)
        return {item['artist'].lower(): item['weighted_score'] for item in data}

def search_concerts(keyword: str = None, city: str = "Austin"):
    """
    Search for upcoming concerts using Ticketmaster Discovery API.
    Can filter by artist keyword or city.
    """
    if not TICKETMASTER_API_KEY:
        return "Error: Ticketmaster API Key is missing."

    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey": TICKETMASTER_API_KEY,
        "city": city,
        "classificationName": "music",
        "size": 50,
        "sort": "date,asc"
    }
    if keyword:
        params["keyword"] = keyword

    response = requests.get(url, params=params)
    if response.status_code != 200:
        return f"Error from Ticketmaster API: {response.status_code}"

    events = response.json().get("_embedded", {}).get("events", [])
    if not events:
        return f"No concerts found for {keyword or 'any artist'} in {city}."

    profile = load_artist_profile()
    results = []
    for event in events:
        name = event.get("name")
        venue_info = event.get("_embedded", {}).get("venues", [{}])[0]
        venue = venue_info.get("name")
        address = venue_info.get("address", {}).get("line1", "")
        city_name = venue_info.get("city", {}).get("name", "")
        date = event.get("dates", {}).get("start", {}).get("localDate")
        url = event.get("url")
        
        score = 0
        for artist, weight in profile.items():
            if artist in name.lower():
                score = weight
                break
        
        results.append({
            "name": name, "venue": venue, "address": f"{address}, {city_name}",
            "date": date, "url": url, "score": score
        })

    results.sort(key=lambda x: (x["score"], x["date"] if x["date"] else ""), reverse=True)
    return results[:10]

def get_distance_to_venue(venue_address: str):
    """
    Calculate the travel time and distance from the user's HOME_ADDRESS to a venue.
    Example: get_distance_to_venue("912 Red River St, Austin, TX")
    """
    if not GMAPS_KEY or "your_" in GMAPS_KEY:
        return "Google Maps API key not configured."
    
    try:
        gmaps = googlemaps.Client(key=GMAPS_KEY)
        result = gmaps.distance_matrix(HOME_ADDRESS, venue_address, mode="driving")
        
        if result['status'] == 'OK':
            element = result['rows'][0]['elements'][0]
            if element['status'] == 'OK':
                distance = element['distance']['text']
                duration = element['duration']['text']
                return f"Distance: {distance}, Travel Time: {duration} from your home ({HOME_ADDRESS})."
        return "Could not calculate distance."
    except Exception as e:
        return f"Error calculating distance: {str(e)}"

def send_concert_sms(message: str):
    """
    Send an SMS alert to the user's phone about a concert.
    """
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_PHONE, MY_PHONE]) or "your_" in TWILIO_SID:
        return "Twilio not fully configured."
    
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        msg = client.messages.create(
            body=f"🎸 Concert Agent: {message}",
            from_=TWILIO_PHONE,
            to=MY_PHONE
        )
        return f"SMS sent successfully! SID: {msg.sid}"
    except Exception as e:
        return f"Error sending SMS: {str(e)}"

def get_venue_details(venue_name: str):
    """
    Look up insider details about an Austin music venue (parking, age limits, vibe, etc.).
    Example: get_venue_details("Stubb's")
    """
    venue_path = Path("data/venue_knowledge.json")
    if not venue_path.exists():
        return "Venue knowledge base not found."
    
    with open(venue_path, 'r') as f:
        knowledge = json.load(f)
    
    # Simple keyword match
    for venue in knowledge:
        if venue_name.lower() in venue['name'].lower():
            return (
                f"--- {venue['name']} Info ---\n"
                f"🅿️ Parking: {venue['parking']}\n"
                f"🔞 Age Limit: {venue['age_limit']}\n"
                f"🎸 Vibe: {venue['vibe']}\n"
                f"💡 Tips: {venue['tips']}"
            )
    
    return f"No specific insider info found for '{venue_name}'. Try asking for Mohawk, Stubb's, Emo's, Moody Center, Empire, or Scoot Inn."
