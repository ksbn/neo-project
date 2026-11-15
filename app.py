import os
from datetime import date, timedelta
import pandas as pd
import psycopg
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="NASA NeoWs Dashboard", page_icon="🌠", layout="wide")

st.title(" NASA Near Earth Objects (NeoWs) Dashboard")
st.markdown("Live data pipeline tracker powered by Python, PostgreSQL, and NASA's NeoWs API.")

@st.cache_data(ttl=300)
def load_data():
    try:
        conn = psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            dbname=os.getenv("DB_NAME", "neo_db"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            port=os.getenv("DB_PORT", "5432"),
            connect_timeout=3
        )
        query = "SELECT neo_reference_id, name, estimated_diameter_min_km, estimated_diameter_max_km, is_potentially_hazardous, close_approach_date, miss_distance_km FROM asteroids ORDER BY close_approach_date DESC;"
        df = pd.read_sql(query, conn)
        conn.close()
        if not df.empty:
            return df
    except Exception:
        pass

    try:
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=3)
        api_key = os.getenv("NASA_API_KEY", "DEMO_KEY")
        url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={start_dt}&end_date={end_dt}&api_key={api_key}"
        
        resp = requests.get(url, timeout=10).json()
        rows = []
        for day, neos in resp.get("near_earth_objects", {}).items():
            for neo in neos:
                approach = neo["close_approach_data"][0]
                rows.append({
                    "neo_reference_id": neo["neo_reference_id"],
                    "name": neo["name"],
                    "estimated_diameter_min_km": neo["estimated_diameter"]["kilometers"]["estimated_diameter_min"],
                    "estimated_diameter_max_km": neo["estimated_diameter"]["kilometers"]["estimated_diameter_max"],
                    "is_potentially_hazardous": neo["is_potentially_hazardous_asteroid"],
                    "close_approach_date": approach["close_approach_date"],
                    "miss_distance_km": float(approach["miss_distance"]["kilometers"])
                })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

df = load_data()

if not df.empty:
    # Metric cards
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Asteroids Tracked", len(df))
    col2.metric("Potentially Hazardous", int(df["is_potentially_hazardous"].sum()))
    col3.metric("Latest Approach Date", str(df["close_approach_date"].max()))

    st.subheader("📋 Asteroid Database Records")
    
    search_query = st.text_input("Search asteroid by name:", "")
    if search_query:
        df = df[df["name"].str.contains(search_query, case=False, na=False)]

    st.dataframe(df, use_container_width=True)
else:
    st.warning("Unable to fetch asteroid data from database or NASA API right now. Please check your network or API limits.")