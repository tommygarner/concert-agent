import sqlite3
import os
from datetime import datetime

class DatabaseManager:
    def __init__(self, db_path="concert_agent.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Table for artist preferences
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artist_preferences (
                    artist_name TEXT PRIMARY KEY,
                    interest_score REAL,
                    status TEXT DEFAULT 'PENDING',
                    last_updated TIMESTAMP
                )
            """)
            # Table for concert alerts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS concert_alerts (
                    event_id TEXT PRIMARY KEY,
                    artist_name TEXT,
                    venue TEXT,
                    date TEXT,
                    url TEXT,
                    notified_status TEXT DEFAULT 'NEW',
                    FOREIGN KEY (artist_name) REFERENCES artist_preferences(artist_name)
                )
            """)
            conn.commit()

    def update_artist_score(self, artist_name, score):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO artist_preferences (artist_name, interest_score, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(artist_name) DO UPDATE SET
                    interest_score = excluded.interest_score,
                    last_updated = excluded.last_updated
            """, (artist_name, score, datetime.now()))
            conn.commit()

    def get_pending_artists(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT artist_name, interest_score FROM artist_preferences WHERE status = 'PENDING'")
            return cursor.fetchall()

    def update_artist_status(self, artist_name, status):
        if status not in ['PENDING', 'APPROVED', 'VETOED']:
            raise ValueError("Invalid status")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE artist_preferences SET status = ? WHERE artist_name = ?", (status, artist_name))
            conn.commit()

    def add_concert_alert(self, event_id, artist_name, venue, date, url):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO concert_alerts (event_id, artist_name, venue, date, url)
                VALUES (?, ?, ?, ?, ?)
            """, (event_id, artist_name, venue, date, url))
            conn.commit()

if __name__ == "__main__":
    db = DatabaseManager()
    print("Database initialized.")
