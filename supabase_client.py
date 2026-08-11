import os
from supabase import create_client, Client
import streamlit as st

@st.cache_resource
def get_supabase_client() -> Client:
    url = None
    key = None

    # Try [supabase] section first
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    except Exception:
        pass

    # Try flat keys
    if not url:
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        except Exception:
            pass

    # Try environment variables
    if not url:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        st.error("❌ Supabase credentials not found. Please check your secrets.toml or Streamlit Cloud secrets.")
        st.info("""
**Expected format in Streamlit Cloud secrets:**
```toml
[supabase]
url = "https://your-project.supabase.co"
key = "your-anon-key"
```
""")
        st.stop()

    return create_client(url, key)
