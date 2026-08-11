import os
from supabase import create_client, Client, ClientOptions
import streamlit as st

@st.cache_resource
def get_supabase_client() -> Client:
    url = None
    key = None

    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    except Exception:
        pass

    if not url:
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        except Exception:
            pass

    if not url:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        st.error("❌ Supabase credentials not found. Check your secrets.")
        st.stop()

    # Set schema at client level - most reliable approach
    client = create_client(
        url, 
        key,
        options=ClientOptions(schema="portfolio")
    )
    return client
