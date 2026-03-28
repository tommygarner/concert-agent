import os
import json
import requests
from dotenv import load_dotenv
from pathlib import Path
from tools import (
    match_artist_to_event, search_concerts as tools_search_concerts,
    get_venue_details, get_recent_setlist, make_gcal_url,
    get_presale_alerts, get_distance_to_venue, search_small_venue_calendar,
    search_side_by_side as tools_search_side_by_side, load_artist_profile
)

load_dotenv()

CITY = os.getenv("CITY", "Austin")

# MCP Wrapper (only runs if fastmcp is installed)
try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("Austin Concert Agent")

    @mcp.tool()
    def search_concerts(keyword: str = None, city: str = CITY, genre: str = None):
        """Search for upcoming concerts using Ticketmaster Discovery API, ranked by listener affinity."""
        return tools_search_concerts(keyword=keyword, city=city, genre=genre)

    @mcp.tool()
    def venue_details(venue_name: str):
        """Look up insider info for an Austin venue (parking, vibe, age limits)."""
        return get_venue_details(venue_name)

    @mcp.tool()
    def recent_setlist(artist_name: str):
        """Fetch the most recent setlist for an artist from setlist.fm."""
        return get_recent_setlist(artist_name)

    @mcp.tool()
    def calendar_url(event_name: str, date: str, venue: str = "", ticket_url: str = ""):
        """Generate a Google Calendar 'Add Event' URL for a concert."""
        return make_gcal_url(event_name, date, venue, ticket_url)

    @mcp.tool()
    def presale_alerts(city: str = CITY):
        """Check for active or upcoming presale windows for superfan artists."""
        return get_presale_alerts(city)

    @mcp.tool()
    def distance_to_venue(venue_address: str):
        """Calculate driving time/distance from home to a venue."""
        return get_distance_to_venue(venue_address)

    @mcp.tool()
    def small_venue_calendar(venue_name: str):
        """Search indie/small venue shows from Showlist Austin + Side By Side Shows."""
        return search_small_venue_calendar(venue_name)

    @mcp.tool()
    def side_by_side_shows():
        """Browse all upcoming indie/niche shows from sidebysideshows.com, ranked by listening history."""
        return tools_search_side_by_side()

    if __name__ == "__main__":
        mcp.run(transport="stdio")
except ImportError:
    if __name__ == "__main__":
        print("FastMCP not installed. Running core search test:")
        print(tools_search_concerts())
