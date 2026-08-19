"""
Import ICICI Direct trade book CSV into Supabase portfolio.transactions
Run: python3 import_trades.py
"""

import csv
import requests
from datetime import datetime

# ── CONFIG — fill these in ────────────────────────────────────────────────────
SUPABASE_URL = "https://iiutxlcayvswjhqgxibd.supabase.co"
SUPABASE_KEY = input("Paste your Supabase anon key: ").strip()
CSV_FILE     = "8501523495_tradeBook.csv"   # put the CSV in the same folder
# ─────────────────────────────────────────────────────────────────────────────

# Stock symbol mapping — ICICI short codes → proper NSE symbols
SYMBOL_MAP = {
    "JIOFIN":  "JIOFIN",
    "DELLIM":  "DELHIVERY",
    "LTFINA":  "LTF",
    "SBFFIN":  "SBFC",
    "IDFBAN":  "IDFCFIRSTB",
    "TATSTE":  "TATASTEEL",
    "ONE97":   "PAYTM",
    "ASHLEY":  "ASHOKLEY",
    "MAHLO":   "MAHLOG",
    "TVSSUP":  "TVSSCS",
}

def parse_date(d):
    return datetime.strptime(d.strip(), "%d-%b-%Y").date().isoformat()

def insert_batch(records):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept-Profile": "portfolio",
        "Content-Profile": "portfolio",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/transactions",
        headers=headers,
        json=records
    )
    if resp.status_code >= 400:
        print(f"  ❌ Error: {resp.status_code} — {resp.text}")
        return False
    return True

def ensure_holding(stock, exchange):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept-Profile": "portfolio",
        "Content-Profile": "portfolio",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    # Check if exists
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/holdings",
        headers=headers,
        params={"stock": f"eq.{stock}", "select": "id"}
    )
    if r.status_code == 200 and r.json():
        return  # already exists
    # Insert
    requests.post(
        f"{SUPABASE_URL}/rest/v1/holdings",
        headers=headers,
        json={"stock": stock, "exchange": exchange}
    )

records = []
skipped = []

with open(CSV_FILE, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        raw_stock = row['Stock'].strip()
        stock = SYMBOL_MAP.get(raw_stock, raw_stock)
        action = row['Action'].strip().upper()
        qty = int(row['Qty'].strip())
        price = float(row['Price'].strip())
        trade_value = float(row['Trade Value'].strip())
        stt = float(row['STT'].strip() or 0)
        sebi = float(row['Transaction and SEBI Turnover charges'].strip() or 0)
        stamp_duty = float(row['Stamp Duty'].strip() or 0)
        brokerage_incl = float(row['Brokerage incl. taxes'].strip() or 0)
        exchange = row['Exchange'].strip() or 'NSE'

        # Use 'Brokerage + Service Tax' column if available (more accurate)
        brok_service_tax = float(row.get('Brokerage + Service Tax', '') or 0)
        if brok_service_tax > 0:
            brokerage_ex_gst = brok_service_tax
            gst = brokerage_incl - brok_service_tax
        else:
            brokerage_ex_gst = brokerage_incl / 1.18
            gst = brokerage_incl - brokerage_ex_gst

        total_charges = stt + sebi + stamp_duty + brokerage_incl
        if action == 'BUY':
            landed_cost = trade_value + total_charges
        else:
            landed_cost = trade_value - total_charges

        effective_unit_price = landed_cost / qty

        record = {
            'date': parse_date(row['Date']),
            'stock': stock,
            'exchange': exchange,
            'action': action,
            'qty': qty,
            'price': round(price, 4),
            'trade_value': round(trade_value, 4),
            'stt': round(stt, 4),
            'stamp_duty': round(stamp_duty, 4),
            'brokerage': round(brokerage_ex_gst, 4),
            'gst_on_brokerage': round(gst, 4),
            'sebi_charges': round(sebi, 4),
            'total_charges': round(total_charges, 4),
            'landed_cost': round(landed_cost, 4),
            'effective_unit_price': round(effective_unit_price, 4),
            'notes': f"Imported from ICICI Direct | Order: {row['Order Ref.'].strip()}"
        }
        records.append(record)

print(f"\n📂 Found {len(records)} transactions to import\n")

# Show preview
print(f"{'Date':<14} {'Stock':<14} {'Action':<6} {'Qty':>5} {'Price':>8} {'Landed Cost':>12}")
print("-" * 65)
for r in records:
    print(f"{r['date']:<14} {r['stock']:<14} {r['action']:<6} {r['qty']:>5} {r['price']:>8.2f} {r['landed_cost']:>12.2f}")

print(f"\nTotal: {len(records)} transactions")
confirm = input("\n✅ Proceed with import? (yes/no): ").strip().lower()

if confirm == 'yes':
    # Ensure holdings rows exist
    stocks_seen = set()
    for r in records:
        if r['stock'] not in stocks_seen:
            ensure_holding(r['stock'], r['exchange'])
            stocks_seen.add(r['stock'])

    # Insert all at once
    success = insert_batch(records)
    if success:
        print(f"\n✅ Successfully imported {len(records)} transactions!")
        print("Open your Streamlit app — all data will be there.")
    else:
        print("\n❌ Import failed. Check the error above.")
else:
    print("Import cancelled.")
