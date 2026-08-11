# 📈 Stock Portfolio Tracker

Personal Indian equity portfolio tracker with STCG/LTCG tax calculation.

**Stack:** Python · Streamlit · Supabase

---

## Features

- ✅ Add Buy/Sell transactions with auto charge calculation (STT, Stamp Duty, Brokerage, GST, SEBI)
- ✅ Holdings summary with CMP, P&L, 25%/50%/100% targets
- ✅ 52-Week Low/High tracking
- ✅ STCG/LTCG tax summary using FIFO (Budget 2024 rates)
- ✅ STCG: 20% | LTCG: 12.5% | Exemption: ₹1.25L/FY
- ✅ Financial Year wise tax breakdown (Apr–Mar)
- ✅ Lot-wise gain/loss detail

---

## Step 1 — Supabase Setup

1. Go to [supabase.com](https://supabase.com) → Create new project
2. Go to **SQL Editor**
3. Copy and paste the entire contents of `schema.sql`
4. Click **Run**
5. Go to **Project Settings → API**
6. Copy your **Project URL** and **anon/public key**

---

## Step 2 — Local Setup (for testing)

```bash
pip install -r requirements.txt

# Create secrets file
mkdir -p .streamlit
# Edit .streamlit/secrets.toml with your Supabase credentials

streamlit run app.py
```

---

## Step 3 — Deploy to Streamlit Cloud

1. Push this folder to a GitHub repository
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Click **New app** → Connect your GitHub repo
4. Set **Main file path** to `app.py`
5. Go to **Advanced settings → Secrets** and paste:

```toml
SUPABASE_URL = "https://your-project-id.supabase.co"
SUPABASE_KEY = "your-anon-public-key"
```

6. Click **Deploy**

---

## Tax Rules Applied

| Type | Rate | Condition |
|------|------|-----------|
| STCG | 20% | Sold within 12 months of purchase |
| LTCG | 12.5% | Sold after 12 months |
| LTCG Exemption | ₹1,25,000 | Per financial year |

- **Method:** FIFO (First In First Out)
- **Financial Year:** April 1 to March 31
- **Rates:** As per Union Budget 2024

---

## Phase 2 (Future) — Add User Auth

Replace open access with Supabase Auth:
- Email + password login
- Row Level Security (RLS) — each user sees only their own data
- Admin view across all users

---

## File Structure

```
stock_tracker/
├── app.py              # Main Streamlit app
├── supabase_client.py  # DB connection
├── tax_engine.py       # FIFO STCG/LTCG calculation
├── requirements.txt
├── schema.sql          # Run this in Supabase SQL Editor
├── README.md
└── .streamlit/
    └── secrets.toml    # Your Supabase credentials (do not commit)
```
