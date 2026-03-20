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
    Fetch top artists from three Spotify time ranges.
    Returns (profile, artist_ids) where:
      profile   = { artist_name_lower: {'score': float, 'tier': str} }
      artist_ids = { artist_name_lower: spotify_artist_id }
    """
    raw = {}       # name_lower -> {score, tier, display_name}
    artist_ids = {}  # name_lower -> spotify_id

    for time_range, multiplier in [
        ("long_term", 0.5),
        ("medium_term", 0.8),
        ("short_term", 1.0),
    ]:
        try:
            results = sp.current_user_top_artists(limit=50, time_range=time_range)
            for rank, artist in enumerate(results["items"]):
                name_lower = artist["name"].lower()
                score = (50 - rank) * multiplier * 5
                if name_lower not in raw or raw[name_lower]["score"] < score:
                    raw[name_lower] = {"score": score, "tier": "fan", "display_name": artist["name"]}
                    artist_ids[name_lower] = artist["id"]
        except Exception:
            pass

    if not raw:
        return {}, {}

    sorted_items = sorted(raw.items(), key=lambda x: x[1]["score"], reverse=True)
    cutoff = max(1, int(len(sorted_items) * _TOP_10_PCT))
    for i, (name, info) in enumerate(sorted_items):
        if i < cutoff:
            info["tier"] = "superfan"

    profile = {name: {"score": info["score"], "tier": info["tier"]} for name, info in raw.items()}
    return profile, artist_ids


def get_related_artists(sp, artist_ids, existing_profile, limit_per_artist=3):
    """
    For the top superfan artists, fetch Spotify related artists not already
    in the user's profile. Returns a list of artist display names for
    use as discovery targets.
    """
    # Pick up to 5 superfan artist IDs to query
    superfan_ids = [
        artist_ids[name] for name in artist_ids
        if existing_profile.get(name, {}).get("tier") == "superfan"
    ][:5]

    discovery = {}
    for artist_id in superfan_ids:
        try:
            results = sp.artist_related_artists(artist_id)
            for related in results["artists"][:limit_per_artist]:
                name_lower = related["name"].lower()
                if name_lower not in existing_profile:
                    discovery[name_lower] = related["name"]
        except Exception:
            pass

    return list(discovery.values())[:15]
