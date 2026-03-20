import json
import os
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from datetime import datetime, timezone

def ingest_spotify_data(data_dir):
    history_dir = Path(data_dir) / "Spotify Extended Streaming History"
    json_files = list(history_dir.glob("Streaming_History_Audio_*.json"))
    
    all_history = []
    
    print(f"Found {len(json_files)} streaming history files.")
    
    for file_path in tqdm(json_files, desc="Processing files"):
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Filter relevant fields and short plays (less than 30s = 30000ms)
            # We keep the timestamp for decay calculation
            filtered = [
                {
                    "ts": item["ts"],
                    "artist": item["master_metadata_album_artist_name"],
                    "track": item["master_metadata_track_name"],
                    "ms_played": item["ms_played"]
                }
                for item in data 
                if item["master_metadata_album_artist_name"] is not None and item["ms_played"] > 30000
            ]
            all_history.extend(filtered)
            
    df = pd.DataFrame(all_history)
    df['ts'] = pd.to_datetime(df['ts'])
    
    # Calculate weight based on recency (Time Decay)
    # Most recent play = 1.0, 10 years ago = much lower
    now = datetime.now(timezone.utc)
    df['days_ago'] = (now - df['ts']).dt.days
    
    # Half-life of 365 days (1 year)
    df['weight'] = 0.5 ** (df['days_ago'] / 365)
    
    # Aggregate by artist
    artist_scores = df.groupby('artist').agg(
        total_plays=('artist', 'count'),
        weighted_score=('weight', 'sum'),
        last_played=('ts', 'max')
    ).sort_values(by='weighted_score', ascending=False)

    # Superfan tiering
    # Recency is measured relative to the most recent play in the dataset,
    # not today — Spotify data exports may be months old.
    # Superfan: top 10% by weighted score AND played within 90 days of the dataset's latest play
    # Fan: top 40% by weighted score OR played within 180 days of the dataset's latest play
    # Casual: everything else
    score_90th = artist_scores['weighted_score'].quantile(0.90)
    score_60th = artist_scores['weighted_score'].quantile(0.60)
    dataset_latest = artist_scores['last_played'].max()
    days_since_played = (dataset_latest - artist_scores['last_played']).dt.days

    def assign_tier(row, days):
        if row['weighted_score'] >= score_90th and days[row.name] <= 90:
            return 'superfan'
        elif row['weighted_score'] >= score_60th or days[row.name] <= 180:
            return 'fan'
        else:
            return 'casual'

    artist_scores['tier'] = artist_scores.apply(
        lambda row: assign_tier(row, days_since_played), axis=1
    )

    # Save to JSON
    os.makedirs("data", exist_ok=True)
    artist_scores.reset_index().to_json("data/artist_profile.json", orient='records', indent=2)

    print(f"\nProfile generated! Top 10 Artists (Weighted):")
    print(artist_scores[['total_plays', 'weighted_score', 'tier']].head(10))

if __name__ == "__main__":
    ingest_spotify_data("my_spotify_data")
