import requests
import os
from dotenv import load_dotenv

load_dotenv()

class TicketmasterScout:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("TICKETMASTER_API_KEY")
        self.base_url = "https://app.ticketmaster.com/discovery/v2"

    def search_concerts(self, artist_name, city="Austin"):
        if not self.api_key:
            print("Ticketmaster API Key not found.")
            return []

        url = f"{self.base_url}/events.json"
        params = {
            "apikey": self.api_key,
            "keyword": artist_name,
            "city": city,
            "classificationName": "music",
            "sort": "date,asc"
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            events = []
            if "_embedded" in data:
                for event in data["_embedded"]["events"]:
                    events.append({
                        "event_id": event["id"],
                        "artist_name": artist_name,
                        "venue": event["_embedded"]["venues"][0]["name"] if "_embedded" in event and "venues" in event["_embedded"] else "Unknown Venue",
                        "date": event["dates"]["start"]["localDate"],
                        "url": event["url"]
                    })
            return events
        except Exception as e:
            print(f"Error searching Ticketmaster for {artist_name}: {e}")
            return []

if __name__ == "__main__":
    # Test with a known artist if key is available
    scout = TicketmasterScout()
    # results = scout.search_concerts("Kacey Musgraves")
    # print(results)
