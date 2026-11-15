import os
import streamlit as st # type: ignore
import psycopg
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

st.set_page_config(page_title="NASA NeoWs Dashboard", page_icon="🌠", layout="wide")

st.title("🌠 NASA Near Earth Objects (NeoWs) Dashboard")
st.markdown("Live data pipeline tracker powered by Python, PostgreSQL, and NASA's NeoWs API.")

@st.cache_data(ttl=60)
def load_data():
    try:
        conn = psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            dbname=os.getenv("DB_NAME", "neo_db"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            port=os.getenv("DB_PORT", "5432")
        )
        query = "SELECT neo_reference_id, name, estimated_diameter_min_km, estimated_diameter_max_km, is_potentially_hazardous, close_approach_date, miss_distance_km FROM asteroids ORDER BY close_approach_date DESC;"
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"Database connection error: {e}")
        return pd.DataFrame()

df = load_data()

if not df.empty:
    # Metric cards
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Asteroids Tracked", len(df))
    col2.metric("Potentially Hazardous", len(df[df["is_potentially_hazardous"] == True]))
    col3.metric("Latest Approach Date", str(df["close_approach_date"].max()))

    st.subheader("📋 Asteroid Database Records")
    
    search_query = st.text_input("Search asteroid by name:", "")
    if search_query:
        df = df[df["name"].str.contains(search_query, case=False, na=False)]

    st.dataframe(df, use_container_width=True)
else:
    st.warning("No data found in database yet. Make sure your Docker container is running and you have executed `python pipeline.py`!")