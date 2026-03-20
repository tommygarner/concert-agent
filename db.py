"""
Supabase persistence layer for the concert agent.

Tables (created via db_setup.sql):
  agent_users     — user identity keyed on Spotify user_id
  clicked_events  — ticket link clicks for cart abandonment follow-up
  attended_events — user-confirmed show attendance

All functions fail silently so a Supabase outage never breaks the UI.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key, default=None):
    try:
        import streamlit as st
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)


def _client():
    from supabase import create_client
    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------

def get_or_create_user(spotify_user_id: str, display_name: str) -> str | None:
    """
    Upsert a user by Spotify user_id. Returns spotify_user_id on success.
    """
    sb = _client()
    if not sb:
        return None
    try:
        sb.table("agent_users").upsert(
            {"spotify_user_id": spotify_user_id, "display_name": display_name},
            on_conflict="spotify_user_id",
        ).execute()
        return spotify_user_id
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Clicked events (cart abandonment)
# ---------------------------------------------------------------------------

def log_click(spotify_user_id: str, event_id: str, event_name: str, venue: str, event_date: str, url: str):
    """Record that the user clicked a ticket link."""
    sb = _client()
    if not sb or not spotify_user_id:
        return
    try:
        sb.table("clicked_events").upsert({
            "spotify_user_id": spotify_user_id,
            "event_id": event_id,
            "event_name": event_name,
            "venue": venue,
            "event_date": event_date,
            "url": url,
        }, on_conflict="spotify_user_id,event_id").execute()
    except Exception:
        pass


def get_unconfirmed_clicks(spotify_user_id: str, days_old: int = 1) -> list:
    """Return clicks older than days_old where purchase wasn't confirmed."""
    sb = _client()
    if not sb or not spotify_user_id:
        return []
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        result = (
            sb.table("clicked_events")
            .select("*")
            .eq("spotify_user_id", spotify_user_id)
            .is_("purchased", "null")
            .lt("clicked_at", cutoff)
            .execute()
        )
        return result.data or []
    except Exception:
        return []


def mark_purchased(spotify_user_id: str, event_id: str, purchased: bool):
    """Mark a clicked event as purchased or dismissed."""
    sb = _client()
    if not sb or not spotify_user_id:
        return
    try:
        sb.table("clicked_events").update(
            {"purchased": purchased}
        ).eq("spotify_user_id", spotify_user_id).eq("event_id", event_id).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Attendance logging
# ---------------------------------------------------------------------------

def log_attendance(spotify_user_id: str, event_name: str, venue: str, event_date: str, attended: bool):
    """Record whether the user attended a show."""
    sb = _client()
    if not sb or not spotify_user_id:
        return
    try:
        sb.table("attended_events").insert({
            "spotify_user_id": spotify_user_id,
            "event_name": event_name,
            "venue": venue,
            "event_date": event_date,
            "attended": attended,
        }).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

def save_message(spotify_user_id: str, role: str, content: str):
    """Save a chat message to Supabase."""
    sb = _client()
    if not sb or not spotify_user_id:
        return
    try:
        sb.table("chat_messages").insert({
            "spotify_user_id": spotify_user_id,
            "role": role,
            "content": content,
        }).execute()
    except Exception:
        pass


def load_chat_history(spotify_user_id: str, limit: int = 20) -> list:
    """Load recent chat messages, oldest first."""
    sb = _client()
    if not sb or not spotify_user_id:
        return []
    try:
        result = (
            sb.table("chat_messages")
            .select("role, content")
            .eq("spotify_user_id", spotify_user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data or []
        rows.reverse()  # oldest first
        return rows
    except Exception:
        return []


def clear_chat_history(spotify_user_id: str):
    """Delete all chat messages for a user."""
    sb = _client()
    if not sb or not spotify_user_id:
        return
    try:
        sb.table("chat_messages").delete().eq("spotify_user_id", spotify_user_id).execute()
    except Exception:
        pass


def get_past_shows(spotify_user_id: str, limit: int = 10) -> list:
    """Return shows the user has logged, most recent first."""
    sb = _client()
    if not sb or not spotify_user_id:
        return []
    try:
        result = (
            sb.table("attended_events")
            .select("*")
            .eq("spotify_user_id", spotify_user_id)
            .order("event_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        return []
