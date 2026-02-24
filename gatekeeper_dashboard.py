import streamlit as st
import pandas as pd
from tools.db_manager import DatabaseManager

st.set_page_config(page_title="Concert Agent Gatekeeper", layout="wide")

st.title("🎵 Artist Approval Gatekeeper")
st.write("Approve or veto artists based on your Spotify listening history.")

db = DatabaseManager()

def load_data():
    with db._get_connection() as conn:
        df = pd.read_sql_query("SELECT artist_name, interest_score, status FROM artist_preferences", conn)
    return df

data = load_data()

# Filter options
status_filter = st.selectbox("Filter by Status", ["PENDING", "APPROVED", "VETOED", "ALL"])
if status_filter != "ALL":
    filtered_data = data[data['status'] == status_filter]
else:
    filtered_data = data

# Sorting
filtered_data = filtered_data.sort_values(by="interest_score", ascending=False)

# Display Table with Actions
for index, row in filtered_data.iterrows():
    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    
    with col1:
        st.subheader(row['artist_name'])
        st.write(f"Score: {row['interest_score']:.2f}")
    
    with col2:
        if st.button("Approve", key=f"app_{row['artist_name']}"):
            db.update_artist_status(row['artist_name'], "APPROVED")
            st.rerun()
            
    with col3:
        if st.button("Veto", key=f"veto_{row['artist_name']}"):
            db.update_artist_status(row['artist_name'], "VETOED")
            st.rerun()
            
    with col4:
        st.write(f"Current: {row['status']}")
    
    st.divider()

if st.button("Refresh Data"):
    st.rerun()
