import pandas as pd
from datetime import datetime, timezone
import spotipy
from spotipy.oauth2 import SpotifyOAuth


class SpotifyAnalystAPI:
    def __init__(self):
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            scope="user-read-recently-played"
        ))

    def get_top_artists(self, decay_half_life_days=90):
        """
        Fetches the 50 most recently played tracks from Spotify API and returns
        artists weighted by recency using an exponential decay function.
        """
        results = self.sp.current_user_recently_played(limit=50)
        items = results.get('items', [])

        rows = []
        for item in items:
            played_at = datetime.fromisoformat(item['played_at'].replace('Z', '+00:00'))
            artist_name = item['track']['artists'][0]['name']
            rows.append({'played_at': played_at, 'artist_name': artist_name})

        if not rows:
            print("No recent plays found.")
            return pd.DataFrame(columns=['artist_name', 'total_score', 'play_count', 'last_played'])

        df = pd.DataFrame(rows)
        df['played_at'] = pd.to_datetime(df['played_at'], utc=True)

        now = datetime.now(timezone.utc)
        df['days_since'] = (now - df['played_at']).dt.total_seconds() / 86400
        df['weight'] = 0.5 ** (df['days_since'] / decay_half_life_days)

        artist_scores = df.groupby('artist_name').agg(
            total_score=('weight', 'sum'),
            play_count=('weight', 'count'),
            last_played=('played_at', 'max')
        ).sort_values(by='total_score', ascending=False)

        return artist_scores.reset_index()


if __name__ == "__main__":
    analyst = SpotifyAnalystAPI()
    top_artists = analyst.get_top_artists()
    print(top_artists.head(20))
