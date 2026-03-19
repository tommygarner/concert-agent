import os
import json
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Config
TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY")
CITY = os.getenv("CITY", "Austin")
STATE_CODE = os.getenv("STATE_CODE", "TX")
PROFILE_PATH = Path("data/artist_profile.json")

def load_artist_profile():
    if not PROFILE_PATH.exists():
        return {}
    with open(PROFILE_PATH, 'r') as f:
        data = json.load(f)
        return {item['artist'].lower(): item['weighted_score'] for item in data}

def core_search_concerts(keyword: str = None, city: str = CITY):
    """The raw logic for searching and ranking concerts."""
    if not TICKETMASTER_API_KEY or "your_ticketmaster_api_key_here" in TICKETMASTER_API_KEY:
        return "Error: Ticketmaster API Key is missing."

    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey": TICKETMASTER_API_KEY,
        "city": city,
        "stateCode": STATE_CODE,
        "classificationName": "music",
        "size": 50,
        "sort": "date,asc"
    }
    if keyword:
        params["keyword"] = keyword

    response = requests.get(url, params=params)
    if response.status_code != 200:
        return f"Error from Ticketmaster API: {response.status_code}"

    data = response.json()
    events = data.get("_embedded", {}).get("events", [])
    
    if not events:
        return f"No concerts found in {city}."

    profile = load_artist_profile()
    results = []
    for event in events:
        name = event.get("name")
        venue = event.get("_embedded", {}).get("venues", [{}])[0].get("name")
        date = event.get("dates", {}).get("start", {}).get("localDate")
        url = event.get("url")
        
        score = 0
        matching_artist = None
        for artist, weight in profile.items():
            if artist in name.lower():
                score = weight
                matching_artist = artist
                break
        
        results.append({
            "name": name, "venue": venue, "date": date,
            "url": url, "score": score, "matched_artist": matching_artist
        })

    results.sort(key=lambda x: (x["score"], x["date"] if x["date"] else ""), reverse=True)
    return results[:15]

# MCP Wrapper (only runs if fastmcp is installed)
try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("Austin Concert Agent")

    @mcp.tool()
    def search_concerts(keyword: str = None, city: str = CITY):
        """Search for upcoming concerts using Ticketmaster Discovery API."""
        return core_search_concerts(keyword, city)

    if __name__ == "__main__":
        mcp.run(transport="stdio")
except ImportError:
    if __name__ == "__main__":
        print("FastMCP not installed. Running core search test:")
        print(core_search_concerts())
