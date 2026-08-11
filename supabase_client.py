import os
import requests
import streamlit as st
from supabase import create_client, Client, ClientOptions

@st.cache_resource
def get_credentials():
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
        st.error("❌ Supabase credentials not found.")
        st.stop()
    return url, key

@st.cache_resource
def get_supabase_client() -> Client:
    url, key = get_credentials()
    return create_client(url, key)

def portfolio_request(method: str, table: str, data=None, params=None, match=None):
    """
    Direct REST call to portfolio schema using Accept-Profile / Content-Profile headers.
    This bypasses supabase-py schema() issues entirely.
    """
    url, key = get_credentials()
    base = f"{url}/rest/v1/{table}"

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept-Profile": "portfolio",
        "Content-Profile": "portfolio",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    resp = requests.request(
        method=method,
        url=base,
        headers=headers,
        json=data,
        params=params
    )

    if resp.status_code >= 400:
        st.error(f"DB Error {resp.status_code}: {resp.text}")
        return []

    if resp.text:
        return resp.json()
    return []


def db_select(table: str, order_by: str = None, filters: dict = None):
    params = {"select": "*"}
    if order_by:
        params["order"] = order_by
    if filters:
        params.update(filters)
    return portfolio_request("GET", table, params=params)


def db_insert(table: str, record: dict):
    return portfolio_request("POST", table, data=record)


def db_update(table: str, record: dict, eq_col: str, eq_val: str):
    params = {eq_col: f"eq.{eq_val}"}
    return portfolio_request("PATCH", table, data=record, params=params)


def db_delete(table: str, eq_col: str, eq_val: str):
    params = {eq_col: f"eq.{eq_val}"}
    return portfolio_request("DELETE", table, params=params)


def db_select_eq(table: str, eq_col: str, eq_val: str):
    params = {"select": "*", eq_col: f"eq.{eq_val}"}
    return portfolio_request("GET", table, params=params)
