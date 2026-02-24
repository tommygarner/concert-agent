from tools.spotify_parser import SpotifyAnalystAPI
from tools.db_manager import DatabaseManager


def sync_data():
    analyst = SpotifyAnalystAPI()
    db = DatabaseManager()

    print("Fetching recent plays from Spotify API...")
    top_artists = analyst.get_top_artists()

    print(f"Found {len(top_artists)} artists. Syncing to database...")
    for _, row in top_artists.iterrows():
        db.update_artist_score(row['artist_name'], row['total_score'])

    print("Sync complete.")


if __name__ == "__main__":
    sync_data()
