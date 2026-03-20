import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

SCOPE = "user-top-read user-read-recently-played"

# Tier thresholds applied to live profile rank-based scores
_TOP_10_PCT = 0.10


def _get_secret(key, default=None):
    """Read from st.secrets first (Streamlit Cloud), fall back to env var."""
    try:
        import streamlit as st
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)


def _make_auth_manager():
    return SpotifyOAuth(
        client_id=_get_secret("SPOTIFY_CLIENT_ID"),
        client_secret=_get_secret("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=_get_secret("SPOTIFY_REDIRECT_URI", "http://localhost:8501"),
        scope=SCOPE,
        cache_path=None,
        open_browser=False,
    )


def get_auth_url():
    """Return the Spotify authorization URL to show to the user."""
    return _make_auth_manager().get_authorize_url()


def exchange_code(code):
    """
    Exchange an auth code for a Spotify client.
    Returns (sp, user_id, display_name) or raises on failure.
    """
    manager = _make_auth_manager()
    token_info = manager.get_access_token(code, as_dict=True, check_cache=False)
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    return sp, user["id"], user.get("display_name") or user["id"]


def build_live_profile(sp):
    """
    Fetch top artists from three Spotify time ranges and return a profile dict
    matching the artist_profile.json schema:
        { artist_name_lower: {'score': float, 'tier': str} }

    Scoring: rank-based (position 1 = highest score). Short-term listens
    weighted more heavily than long-term to capture current taste.
    """
    raw = {}

    for time_range, multiplier in [
        ("long_term", 0.5),    # ~years of history
        ("medium_term", 0.8),  # ~6 months
        ("short_term", 1.0),   # ~4 weeks
    ]:
        try:
            results = sp.current_user_top_artists(limit=50, time_range=time_range)
            for rank, artist in enumerate(results["items"]):
                name_lower = artist["name"].lower()
                # Scale scores to feel roughly comparable to the 10-year weighted scores
                score = (50 - rank) * multiplier * 5
                if name_lower not in raw or raw[name_lower]["score"] < score:
                    raw[name_lower] = {
                        "score": score,
                        "tier": "fan",
                        "display_name": artist["name"],
                    }
        except Exception:
            pass

    if not raw:
        return {}

    # Assign superfan tier to top 10% by score
    sorted_items = sorted(raw.items(), key=lambda x: x[1]["score"], reverse=True)
    cutoff = max(1, int(len(sorted_items) * _TOP_10_PCT))
    for i, (name, info) in enumerate(sorted_items):
        if i < cutoff:
            info["tier"] = "superfan"

    return {name: {"score": info["score"], "tier": info["tier"]} for name, info in raw.items()}
