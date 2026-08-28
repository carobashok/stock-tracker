import streamlit as st
import pandas as pd
from datetime import date, datetime
from supabase_client import db_select, db_insert, db_update, db_delete, db_select_eq
from tax_engine import (
    compute_tax_lots,
    compute_fy_tax_summary,
    compute_holdings_from_transactions,
    get_financial_year,
    STCG_RATE,
    LTCG_RATE,
    LTCG_EXEMPTION
)

st.set_page_config(
    page_title="Stock Portfolio Tracker",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .metric-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        text-align: center;
    }
    .metric-label { font-size: 0.78rem; color: #64748b; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-value { font-size: 1.5rem; font-weight: 600; color: #0f172a; }
    .metric-sub   { font-size: 0.8rem; margin-top: 2px; }
    .gain  { color: #16a34a; }
    .loss  { color: #dc2626; }
    .neutral { color: #64748b; }
    .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_inr(val):
    if val is None: return "—"
    val = float(val)
    sign = "-" if val < 0 else ""
    val = abs(val)
    if val >= 1e7:   return f"{sign}₹{val/1e7:.2f}Cr"
    if val >= 1e5:   return f"{sign}₹{val/1e5:.2f}L"
    return f"{sign}₹{val:,.2f}"

def pnl_color(val):
    if val is None: return "neutral"
    return "gain" if float(val) >= 0 else "loss"

def calculate_charges(action, qty, price, broker='ICICI Direct (0.29%)', exchange='NSE'):
    trade_value = qty * price
    # STT: 0.1% buy, 0.025% sell (delivery)
    stt = trade_value * 0.001 if action == 'BUY' else trade_value * 0.00025
    # Stamp duty: 0.015% on buy only
    stamp_duty = trade_value * 0.00015 if action == 'BUY' else 0.0
    # SEBI charges
    sebi = trade_value * 0.000001
    # Exchange transaction charges
    txn_charge = trade_value * 0.0000325 if exchange == 'NSE' else trade_value * 0.0000375
    # Brokerage by plan
    if broker == 'ICICI Direct (0.29%)':
        brokerage = trade_value * 0.0029
    elif broker == 'ICICI Direct Prime (0.15%)':
        brokerage = trade_value * 0.0015
    elif broker == 'ICICI Direct Prime (0.07%)':
        brokerage = trade_value * 0.0007
    elif broker == 'Zerodha / Flat Rs.20':
        brokerage = 20.0
    elif broker == 'Angel One (Free Delivery)':
        brokerage = 0.0
    else:
        brokerage = trade_value * 0.0029
    # GST 18% on brokerage + txn charges
    gst = (brokerage + txn_charge) * 0.18
    # DP charges on SELL for ICICI: 0.04% min Rs.30
    dp_charges = 0.0
    if action == 'SELL' and 'ICICI' in broker:
        dp_charges = max(30.0, trade_value * 0.0004)
    total_charges = stt + stamp_duty + brokerage + gst + sebi + txn_charge + dp_charges
    landed_cost = trade_value + total_charges if action == 'BUY' else trade_value - total_charges
    effective_unit_price = landed_cost / qty
    return {
        'trade_value': round(trade_value, 4),
        'stt': round(stt, 4),
        'stamp_duty': round(stamp_duty, 4),
        'brokerage': round(brokerage, 4),
        'gst_on_brokerage': round(gst, 4),
        'sebi_charges': round(sebi, 4),
        'txn_charges': round(txn_charge, 4),
        'dp_charges': round(dp_charges, 4),
        'total_charges': round(total_charges, 4),
        'landed_cost': round(landed_cost, 4),
        'effective_unit_price': round(effective_unit_price, 4),
    }

def metric_card(col, label, value, sub=None, sub_class="neutral"):
    col.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {'<div class="metric-sub ' + sub_class + '">' + sub + '</div>' if sub else ''}
    </div>""", unsafe_allow_html=True)

# ── Live Price Fetcher (Yahoo Finance) ───────────────────────────────────────
def fetch_live_price(symbol: str):
    """Fetch live price from Yahoo Finance. Tries NSE (.NS) then BSE (.BO)."""
    import requests as req
    def _fetch(ticker):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            resp = req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            result = data.get("chart", {}).get("result")
            if not result: return None
            meta = result[0]["meta"]
            price = meta.get("regularMarketPrice")
            prev  = meta.get("chartPreviousClose")
            if not price: return None
            change_pct = ((price - prev) / prev * 100) if prev else 0
            return {"price": price, "change_pct": round(change_pct, 2), "ticker": ticker}
        except Exception:
            return None

    return _fetch(f"{symbol}.NS") or _fetch(f"{symbol}.BO") or None

# ── Data fetchers ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def fetch_transactions():
    return db_select('transactions', order_by='date.desc')

@st.cache_data(ttl=60)
def fetch_holdings_meta():
    rows = db_select('holdings')
    return {r['stock']: r for r in rows}

def invalidate_cache():
    fetch_transactions.clear()
    fetch_holdings_meta.clear()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 Portfolio Tracker")
    st.markdown("---")
    page = st.radio(
        "Navigate",
        ["🏠 Dashboard", "➕ Add Transaction", "📋 Transactions", "💼 Holdings", "🧾 Tax Summary", "🚀 IPO Tracker"],
        label_visibility="collapsed"
    )
    st.markdown("---")
    st.caption("Indian Equity · FIFO · Budget 2024")
    st.caption(f"STCG: {int(STCG_RATE*100)}%  |  LTCG: {int(LTCG_RATE*100)}%")
    st.caption("LTCG Exemption: ₹1.25L/FY")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.title("📊 Portfolio Dashboard")

    txns = fetch_transactions()
    holdings_meta = fetch_holdings_meta()

    if not txns:
        st.info("No transactions yet. Go to **Add Transaction** to get started.")
        st.stop()

    holdings = compute_holdings_from_transactions(txns)

    if not holdings:
        st.info("No open holdings found.")
        st.stop()

    # ── Live CMP Fetch from Yahoo Finance ────────────────────────────────────
    col_sync, col_info = st.columns([1, 3])
    with col_sync:
        if st.button("🔄 Fetch Live Prices", type="primary", use_container_width=True):
            synced = 0
            not_found = []
            with st.spinner("Fetching live prices from Yahoo Finance..."):
                for stock in holdings.keys():
                    price_data = fetch_live_price(stock)
                    if price_data:
                        payload = {
                            'cmp': round(price_data['price'], 2),
                            'cmp_updated_at': datetime.utcnow().isoformat()
                        }
                        existing = db_select_eq('holdings', 'stock', stock)
                        if existing:
                            db_update('holdings', payload, 'stock', stock)
                        else:
                            db_insert('holdings', {'stock': stock, 'exchange': 'NSE', **payload})
                        synced += 1
                    else:
                        not_found.append(stock)
            invalidate_cache()
            if synced:
                st.success(f"✅ Live prices fetched for {synced} stock(s)!")
            if not_found:
                st.warning(f"Could not fetch: {', '.join(not_found)} — update manually below.")
            st.rerun()
    with col_info:
        st.caption("Fetches live prices directly from Yahoo Finance (NSE). No manual steps needed.")

    # CMP Update (Manual)
    with st.expander("📡 Update CMP Manually", expanded=False):
        cols = st.columns(min(len(holdings), 4))
        for i, (stock, h) in enumerate(holdings.items()):
            meta = holdings_meta.get(stock, {})
            with cols[i % min(len(holdings), 4)]:
                new_cmp = st.number_input(
                    f"{stock}", value=float(meta.get('cmp') or h['avg_cost']),
                    step=0.05, format="%.2f", key=f"cmp_{stock}"
                )
                if st.button("Update", key=f"upd_{stock}"):
                    payload = {'cmp': new_cmp, 'cmp_updated_at': datetime.utcnow().isoformat()}
                    existing = db_select_eq('holdings', 'stock', stock)
                    if existing:
                        db_update('holdings', payload, 'stock', stock)
                    else:
                        db_insert('holdings', {'stock': stock, 'exchange': 'NSE', **payload})
                    invalidate_cache()
                    st.success(f"CMP updated for {stock}")
                    st.rerun()

    holdings_meta = fetch_holdings_meta()

    total_invested = 0
    total_current = 0
    rows = []

    for stock, h in holdings.items():
        meta = holdings_meta.get(stock, {})
        cmp = float(meta.get('cmp') or h['avg_cost'])
        invested = h['total_cost']
        current_val = h['qty'] * cmp
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100) if invested else 0
        total_invested += invested
        total_current += current_val
        qty  = h['qty']
        avg  = h['avg_cost']
        rows.append({
            'Stock': stock,
            'Qty': qty,
            'Avg Cost': f"₹{avg:,.2f}",
            'CMP': f"₹{cmp:,.2f}",
            'Invested': fmt_inr(invested),
            'Current Value': fmt_inr(current_val),
            'P&L (₹)': fmt_inr(pnl),
            'P&L (%)': f"{pnl_pct:+.2f}%",
            '52W Low': f"₹{float(meta['week_52_low']):,.2f}" if meta.get('week_52_low') else "—",
            '52W High': f"₹{float(meta['week_52_high']):,.2f}" if meta.get('week_52_high') else "—",
            # Per share price targets
            '25% Price': f"₹{avg*1.25:,.2f}",
            '50% Price': f"₹{avg*1.50:,.2f}",
            '100% Price': f"₹{avg*2.00:,.2f}",
            # Total portfolio value at each target
            '25% Value': fmt_inr(avg * 1.25 * qty),
            '50% Value': fmt_inr(avg * 1.50 * qty),
            '100% Value': fmt_inr(avg * 2.00 * qty),
            # Gain amount at each target
            '25% Gain': fmt_inr(avg * 0.25 * qty),
            '50% Gain': fmt_inr(avg * 0.50 * qty),
            '100% Gain': fmt_inr(avg * 1.00 * qty),
        })

    total_pnl = total_current - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0

    c1, c2, c3, c4 = st.columns(4)
    metric_card(c1, "Total Invested", fmt_inr(total_invested))
    metric_card(c2, "Current Value", fmt_inr(total_current))
    metric_card(c3, "Overall P&L", fmt_inr(total_pnl), f"{total_pnl_pct:+.2f}%", pnl_color(total_pnl))
    metric_card(c4, "Stocks Held", str(len(holdings)))

    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("Holdings")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Current FY Tax Snapshot
    st.subheader("Current FY Tax Snapshot")
    current_fy = get_financial_year(date.today())
    all_lots = compute_tax_lots(txns)
    fy_summary = compute_fy_tax_summary(all_lots)

    if current_fy in fy_summary:
        s = fy_summary[current_fy]
        t1, t2, t3, t4 = st.columns(4)
        metric_card(t1, f"STCG ({current_fy})", fmt_inr(s['stcg_gain']), f"Tax: {fmt_inr(s['stcg_tax'])}", pnl_color(s['stcg_gain']))
        metric_card(t2, f"LTCG ({current_fy})", fmt_inr(s['ltcg_gain']), f"Tax: {fmt_inr(s['ltcg_tax'])}", pnl_color(s['ltcg_gain']))
        metric_card(t3, "LTCG Exempt", fmt_inr(s['ltcg_exempt']), "₹1.25L/FY", "neutral")
        metric_card(t4, "Est. Total Tax", fmt_inr(s['total_tax']), current_fy, pnl_color(-s['total_tax']))
    else:
        st.info(f"No realised gains in {current_fy} yet.")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: ADD TRANSACTION
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "➕ Add Transaction":
    st.title("➕ Add Transaction")

    # ── Import from ICICI CSV ─────────────────────────────────────────────────
    with st.expander("📂 Import from ICICI Direct Trade Book CSV", expanded=True):
        uploaded_csv = st.file_uploader("Upload your tradeBook.csv from ICICI Direct", type=["csv"])
        if uploaded_csv:
            import io
            df_csv = pd.read_csv(uploaded_csv)
            df_csv.columns = df_csv.columns.str.strip()
            df_csv['Date'] = pd.to_datetime(df_csv['Date'].str.strip(), format="%d-%b-%Y")

            SYMBOL_MAP = {
                "JIOFIN":"JIOFIN","DELLIM":"DELHIVERY","LTFINA":"LTF",
                "SBFFIN":"SBFC","IDFBAN":"IDFCFIRSTB","TATSTE":"TATASTEEL",
                "ONE97":"PAYTM","ASHLEY":"ASHOKLEY","MAHLO":"MAHLOG","TVSSUP":"TVSSCS"
            }

            records_to_import = []
            for _, row in df_csv.iterrows():
                raw_stock = str(row['Stock']).strip()
                stock_sym = SYMBOL_MAP.get(raw_stock, raw_stock)
                action_val = str(row['Action']).strip().upper()
                qty_val = int(row['Qty'])
                price_val = float(row['Price'])
                trade_val = float(row['Trade Value'])
                stt_val = float(str(row['STT']).strip() or 0)
                sebi_val = float(str(row['Transaction and SEBI Turnover charges']).strip() or 0)
                stamp_val = float(str(row['Stamp Duty']).strip() or 0)
                # ICICI formula: STT + SEBI + Stamp Duty + (Brokerage + Service Tax)
                brokerage = float(str(row.get('Brokerage + Service Tax', '') or '').strip() or 0)
                brokerage_incl = float(str(row['Brokerage incl. taxes']).strip() or 0)
                total_charges = stt_val + sebi_val + stamp_val + brokerage
                landed = trade_val + total_charges if action_val == 'BUY' else trade_val - total_charges
                eff_price = landed / qty_val
                records_to_import.append({
                    'date': row['Date'].date().isoformat(),
                    'stock': stock_sym,
                    'exchange': str(row.get('Exchange', 'NSE')).strip(),
                    'action': action_val,
                    'qty': qty_val,
                    'price': round(price_val, 4),
                    'trade_value': round(trade_val, 4),
                    'stt': round(stt_val, 4),
                    'stamp_duty': round(stamp_val, 4),
                    'brokerage': round(brokerage, 4),
                    'gst_on_brokerage': round(brokerage_incl - brokerage, 4),
                    'sebi_charges': round(sebi_val, 4),
                    'total_charges': round(total_charges, 4),
                    'landed_cost': round(landed, 4),
                    'effective_unit_price': round(eff_price, 4),
                    'notes': f"Imported from ICICI Direct | Order: {str(row.get('Order Ref.', '')).strip()}"
                })

            # Preview
            preview_df = pd.DataFrame([{
                'Date': r['date'], 'Stock': r['stock'], 'Action': r['action'],
                'Qty': r['qty'], 'Price': f"₹{r['price']:,.2f}",
                'Landed Cost': f"₹{r['landed_cost']:,.2f}",
                'Eff. Price': f"₹{r['effective_unit_price']:,.2f}"
            } for r in records_to_import])
            st.dataframe(preview_df, use_container_width=True, hide_index=True)

            if st.button("✅ Import All Transactions", type="primary"):
                imported = 0
                for record in records_to_import:
                    db_insert('transactions', record)
                    existing = db_select_eq('holdings', 'stock', record['stock'])
                    if not existing:
                        db_insert('holdings', {'stock': record['stock'], 'exchange': record['exchange']})
                    imported += 1
                invalidate_cache()
                st.success(f"✅ {imported} transaction(s) imported successfully!")
                st.balloons()

    st.markdown("---")
    st.subheader("Or Enter Manually")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Transaction Details")
        txn_date = st.date_input("Date", value=date.today(), max_value=date.today())
        stock = st.text_input("Stock Symbol", placeholder="e.g. JIOFIN, SBFC, HDFCBANK").upper().strip()
        exchange = st.selectbox("Exchange", ["NSE", "BSE"])
        action = st.radio("Action", ["BUY", "SELL"], horizontal=True)
        qty = st.number_input("Quantity (shares)", min_value=1, step=1, value=1)
        price = st.number_input("Price per share (₹)", min_value=0.01, step=0.05, format="%.4f")
        broker = st.selectbox("Broker Plan", [
            'ICICI Direct (0.29%)',
            'ICICI Direct Prime (0.15%)',
            'ICICI Direct Prime (0.07%)',
            'Zerodha / Flat Rs.20',
            'Angel One (Free Delivery)'
        ])
        notes = st.text_input("Notes (optional)")

    with col2:
        st.subheader("Charge Breakdown")
        if stock and qty > 0 and price > 0:
            charges = calculate_charges(action, qty, price, broker, exchange)
            st.markdown(f"""
| Component | Amount |
|---|---|
| Trade Value | ₹{charges['trade_value']:,.4f} |
| STT | ₹{charges['stt']:,.4f} |
| Stamp Duty | ₹{charges['stamp_duty']:,.4f} |
| Brokerage | ₹{charges['brokerage']:,.4f} |
| Txn Charges | ₹{charges['txn_charges']:,.4f} |
| GST (Brokerage+Txn) | ₹{charges['gst_on_brokerage']:,.4f} |
| SEBI Charges | ₹{charges['sebi_charges']:,.4f} |
| DP Charges (Sell only) | ₹{charges['dp_charges']:,.4f} |
| **Total Charges** | **₹{charges['total_charges']:,.4f}** |
| **Landed Cost** | **₹{charges['landed_cost']:,.4f}** |
| **Eff. Unit Price** | **₹{charges['effective_unit_price']:,.4f}** |
""")
            st.markdown(f"**Financial Year:** {get_financial_year(txn_date)}")

            if st.button("✅ Save Transaction", type="primary", use_container_width=True):
                if not stock:
                    st.error("Please enter a stock symbol.")
                else:
                    record = {
                        'date': txn_date.isoformat(),
                        'stock': stock,
                        'exchange': exchange,
                        'action': action,
                        'qty': qty,
                        'price': price,
                        'notes': notes,
                        **charges
                    }
                    db_insert('transactions', record)
                    existing = db_select_eq('holdings', 'stock', stock)
                    if not existing:
                        db_insert('holdings', {'stock': stock, 'exchange': exchange})
                    invalidate_cache()
                    st.success(f"✅ {action} {qty} shares of {stock} @ ₹{price:.2f} saved!")
                    st.balloons()
        else:
            st.info("Enter stock, quantity and price to see charge breakdown.")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: TRANSACTIONS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Transactions":
    st.title("📋 Transaction History")

    txns = fetch_transactions()

    if not txns:
        st.info("No transactions yet.")
        st.stop()

    df = pd.DataFrame(txns)
    df['date'] = pd.to_datetime(df['date'])

    col1, col2, col3 = st.columns(3)
    with col1:
        sel_stock = st.selectbox("Filter by Stock", ['All'] + sorted(df['stock'].unique().tolist()))
    with col2:
        sel_action = st.selectbox("Filter by Action", ['All', 'BUY', 'SELL'])
    with col3:
        years = sorted(df['date'].dt.year.unique().tolist(), reverse=True)
        sel_year = st.selectbox("Filter by Year", ['All'] + [str(y) for y in years])

    filtered = df.copy()
    if sel_stock != 'All': filtered = filtered[filtered['stock'] == sel_stock]
    if sel_action != 'All': filtered = filtered[filtered['action'] == sel_action]
    if sel_year != 'All': filtered = filtered[filtered['date'].dt.year == int(sel_year)]
    filtered = filtered.sort_values('date', ascending=False)

    # Build clean display dataframe with readable columns
    display = pd.DataFrame({
        'Date': filtered['date'].dt.strftime('%d-%b-%Y'),
        'Stock': filtered['stock'],
        'Exch': filtered['exchange'] if 'exchange' in filtered.columns else 'NSE',
        'Action': filtered['action'],
        'Qty': filtered['qty'],
        'Price (₹)': filtered['price'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Trade Value (₹)': filtered['trade_value'].apply(lambda x: f"₹{float(x):,.2f}"),
        'STT (₹)': filtered['stt'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Stamp Duty (₹)': filtered['stamp_duty'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Brokerage (₹)': filtered['brokerage'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Total Charges (₹)': filtered['total_charges'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Landed Cost (₹)': filtered['landed_cost'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Eff. Price (₹)': filtered['effective_unit_price'].apply(lambda x: f"₹{float(x):,.2f}"),
        'Notes': filtered['notes'] if 'notes' in filtered.columns else '',
    })

    def highlight_action(row):
        if row.get('Action') == 'BUY':
            return ['background-color: #f0fdf4; color:#166534'] * len(row)
        elif row.get('Action') == 'SELL':
            return ['background-color: #fff1f2; color:#dc2626'] * len(row)
        return [''] * len(row)

    st.dataframe(display.style.apply(highlight_action, axis=1), use_container_width=True, hide_index=True)
    st.caption(f"Showing {len(filtered)} of {len(df)} transactions")

    with st.expander("🗑️ Delete a Transaction"):
        st.warning("This cannot be undone.")
        txn_options = {f"{r['date']} | {r['stock']} | {r['action']} | {r['qty']} shares": r['id'] for r in txns}
        sel_label = st.selectbox("Select transaction", list(txn_options.keys()))
        if st.button("Delete", type="secondary"):
            db_delete('transactions', 'id', txn_options[sel_label])
            invalidate_cache()
            st.success("Deleted.")
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: HOLDINGS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💼 Holdings":
    st.title("💼 Holdings")

    txns = fetch_transactions()
    holdings_meta = fetch_holdings_meta()

    if not txns:
        st.info("No transactions yet.")
        st.stop()

    holdings = compute_holdings_from_transactions(txns)

    if not holdings:
        st.info("No open holdings.")
        st.stop()

    st.subheader("Holdings Summary")
    holdings_meta = fetch_holdings_meta()
    rows = []
    for stock, h in holdings.items():
        meta = holdings_meta.get(stock, {})
        cmp = float(meta.get('cmp') or h['avg_cost'])
        current_val = h['qty'] * cmp
        pnl = current_val - h['total_cost']
        pnl_pct = (pnl / h['total_cost'] * 100) if h['total_cost'] else 0
        qty = h['qty']
        avg  = h['avg_cost']
        rows.append({
            'Stock': stock,
            'Qty': qty,
            'Avg Cost (₹)': round(avg, 2),
            'CMP (₹)': round(cmp, 2),
            'Invested (₹)': round(h['total_cost'], 2),
            'Current Value (₹)': round(current_val, 2),
            'P&L (₹)': round(pnl, 2),
            'P&L (%)': round(pnl_pct, 2),
            '52W Low': round(float(meta['week_52_low']), 2) if meta.get('week_52_low') else None,
            '52W High': round(float(meta['week_52_high']), 2) if meta.get('week_52_high') else None,
            # Per share targets
            '25% Price (₹)': round(avg * 1.25, 2),
            '50% Price (₹)': round(avg * 1.50, 2),
            '100% Price (₹)': round(avg * 2.00, 2),
            # Total value targets
            '25% Value (₹)': round(avg * 1.25 * qty, 2),
            '50% Value (₹)': round(avg * 1.50 * qty, 2),
            '100% Value (₹)': round(avg * 2.00 * qty, 2),
            # Gain at each target
            '25% Gain (₹)': round(avg * 0.25 * qty, 2),
            '50% Gain (₹)': round(avg * 0.50 * qty, 2),
            '100% Gain (₹)': round(avg * 1.00 * qty, 2),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Holding Period Analysis ───────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📅 Holding Period Analysis")
    st.caption("Shows each buy lot — days held and STCG/LTCG status if sold today")

    today = date.today()
    holdings_meta2 = fetch_holdings_meta()

    for stock, h in holdings.items():
        meta = holdings_meta2.get(stock, {})
        cmp = float(meta.get('cmp') or h['avg_cost'])

        # Count LTCG and STCG qty
        ltcg_qty = sum(lot['qty'] for lot in h['lots'] if (today - lot['date']).days > 365)
        stcg_qty = sum(lot['qty'] for lot in h['lots'] if (today - lot['date']).days <= 365)
        ltcg_val = ltcg_qty * cmp
        stcg_val = stcg_qty * cmp

        # Header
        col_h1, col_h2, col_h3 = st.columns([2, 1, 1])
        with col_h1:
            st.markdown(f"#### {stock}")
        with col_h2:
            st.markdown(f"🟢 **LTCG: {ltcg_qty} shares** ({fmt_inr(ltcg_val)})")
        with col_h3:
            st.markdown(f"🔴 **STCG: {stcg_qty} shares** ({fmt_inr(stcg_val)})")

        # Lot-wise table
        lot_rows = []
        for lot in h['lots']:
            days_held = (today - lot['date']).days
            is_ltcg = days_held > 365
            ltcg_date = date(lot['date'].year + 1, lot['date'].month, lot['date'].day)
            gain_type = '🟢 LTCG' if is_ltcg else '🔴 STCG'
            current_val = lot['qty'] * cmp
            cost = lot['qty'] * lot['landed_cost_per_unit']
            pnl = current_val - cost

            lot_rows.append({
                'Buy Date': lot['date'].strftime('%d-%b-%Y'),
                'Qty': lot['qty'],
                'Cost/Share (₹)': round(lot['landed_cost_per_unit'], 2),
                'Days Held': days_held,
                'Status': gain_type,
                'LTCG From': ltcg_date.strftime('%d-%b-%Y') if not is_ltcg else '✅ Already LTCG',
                'Current Value (₹)': round(current_val, 2),
                'Unrealised P&L (₹)': round(pnl, 2),
            })

        lot_df = pd.DataFrame(lot_rows)

        def color_status(val):
            if 'LTCG' in str(val) and '🟢' in str(val):
                return 'background-color: #dcfce7; color: #166534; font-weight:600'
            if 'STCG' in str(val):
                return 'background-color: #fef3c7; color: #92400e; font-weight:600'
            return ''

        def color_pnl(val):
            if isinstance(val, (int, float)):
                return f"color: {'#16a34a' if val >= 0 else '#dc2626'}; font-weight:600"
            return ''

        st.dataframe(
            lot_df.style
                .map(color_status, subset=['Status'])
                .map(color_pnl, subset=['Unrealised P&L (₹)']),
            use_container_width=True,
            hide_index=True
        )
        st.markdown("")

    # ── 52-Week Range Update (bottom) ─────────────────────────────────────────
    st.markdown("---")
    with st.expander("📊 Update 52-Week Low / High", expanded=False):
        cols = st.columns(min(len(holdings), 3))
        holdings_meta3 = fetch_holdings_meta()
        for i, stock in enumerate(holdings):
            meta = holdings_meta3.get(stock, {})
            with cols[i % 3]:
                with st.form(key=f"form_52w_{stock}"):
                    st.markdown(f"**{stock}**")
                    low = st.number_input("52W Low", value=float(meta.get('week_52_low') or 0), step=0.05, format="%.2f")
                    high = st.number_input("52W High", value=float(meta.get('week_52_high') or 0), step=0.05, format="%.2f")
                    if st.form_submit_button("Save"):
                        existing = db_select_eq('holdings', 'stock', stock)
                        if existing:
                            db_update('holdings', {'week_52_low': low, 'week_52_high': high}, 'stock', stock)
                        else:
                            db_insert('holdings', {'stock': stock, 'week_52_low': low, 'week_52_high': high})
                        invalidate_cache()
                        st.success(f"Saved for {stock}")
                        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: TAX SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🧾 Tax Summary":
    st.title("🧾 STCG / LTCG Tax Summary")
    st.caption("FIFO · Budget 2024 · STCG 20% · LTCG 12.5% · Exemption ₹1.25L/FY")

    txns = fetch_transactions()

    if not txns:
        st.info("No transactions yet.")
        st.stop()

    all_lots = compute_tax_lots(txns)

    if not all_lots:
        st.info("No realised gains/losses yet. Add sell transactions to see tax summary.")
        st.stop()

    fy_summary = compute_fy_tax_summary(all_lots)
    fy_list = sorted(fy_summary.keys(), reverse=True)
    sel_fy = st.selectbox("Select Financial Year", fy_list)
    s = fy_summary[sel_fy]

    c1, c2, c3, c4, c5 = st.columns(5)
    metric_card(c1, "STCG Gain", fmt_inr(s['stcg_gain']), f"@{s['stcg_rate']}% = {fmt_inr(s['stcg_tax'])}", pnl_color(s['stcg_gain']))
    metric_card(c2, "LTCG Gain", fmt_inr(s['ltcg_gain']), f"@{s['ltcg_rate']}% = {fmt_inr(s['ltcg_tax'])}", pnl_color(s['ltcg_gain']))
    metric_card(c3, "LTCG Exempt", fmt_inr(s['ltcg_exempt']), "₹1.25L limit", "neutral")
    metric_card(c4, "LTCG Taxable", fmt_inr(s['ltcg_taxable']), "After exemption", pnl_color(s['ltcg_taxable']))
    metric_card(c5, "Est. Tax", fmt_inr(s['total_tax']), sel_fy, "loss" if s['total_tax'] > 0 else "neutral")

    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("Lot-wise Breakdown")
    lots_df = pd.DataFrame([l for l in all_lots if l['financial_year'] == sel_fy])

    if not lots_df.empty:
        lots_df['sell_date'] = pd.to_datetime(lots_df['sell_date']).dt.strftime('%d-%b-%Y')
        lots_df['buy_date'] = pd.to_datetime(lots_df['buy_date']).dt.strftime('%d-%b-%Y')
        display_lots = lots_df[[
            'stock', 'buy_date', 'sell_date', 'holding_days',
            'qty_sold', 'buy_price', 'sell_price',
            'buy_landed_cost', 'sell_proceeds', 'gain_amount', 'gain_type', 'tax_rate'
        ]].copy()
        display_lots.columns = [
            'Stock', 'Buy Date', 'Sell Date', 'Days Held',
            'Qty', 'Buy Price (₹)', 'Sell Price (₹)',
            'Buy Cost (₹)', 'Sell Proceeds (₹)', 'Gain/Loss (₹)', 'Type', 'Tax Rate (%)'
        ]

        def color_type(val):
            if val == 'STCG': return 'background-color: #fef3c7; color: #92400e; font-weight:600'
            if val == 'LTCG': return 'background-color: #dcfce7; color: #166534; font-weight:600'
            return ''

        def color_gain(val):
            if isinstance(val, (int, float)):
                return f"color: {'#16a34a' if val >= 0 else '#dc2626'}; font-weight:600"
            return ''

        st.dataframe(
            display_lots.style.map(color_type, subset=['Type']).map(color_gain, subset=['Gain/Loss (₹)']),
            use_container_width=True, hide_index=True
        )

    st.markdown("---")
    st.subheader("All Years Summary")
    fy_rows = []
    for fy, s in sorted(fy_summary.items(), reverse=True):
        fy_rows.append({
            'FY': fy,
            'STCG Gain (₹)': round(s['stcg_gain'], 2),
            'STCG Tax (₹)': round(s['stcg_tax'], 2),
            'LTCG Gain (₹)': round(s['ltcg_gain'], 2),
            'LTCG Exempt (₹)': round(s['ltcg_exempt'], 2),
            'LTCG Taxable (₹)': round(s['ltcg_taxable'], 2),
            'LTCG Tax (₹)': round(s['ltcg_tax'], 2),
            'Total Tax (₹)': round(s['total_tax'], 2),
        })
    st.dataframe(pd.DataFrame(fy_rows), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: IPO TRACKER
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🚀 IPO Tracker":
    st.title("🚀 IPO Tracker")
    st.caption("Track IPO applications → allotment → listing → ongoing transactions")

    tab1, tab2 = st.tabs(["📋 My IPOs", "➕ Add IPO Application"])

    # ── TAB 1: MY IPOs ────────────────────────────────────────────────────────
    with tab1:
        ipos = db_select('ipo_tracker', order_by='application_date.desc')

        if not ipos:
            st.info("No IPO applications yet. Go to 'Add IPO Application' tab to get started.")
        else:
            for ipo in ipos:
                status = ipo.get('status', 'Applied')
                status_color = {
                    'Applied': '🟡', 'Allotted': '🟢',
                    'Not Allotted': '🔴', 'Listed': '🔵'
                }.get(status, '⚪')

                with st.expander(f"{status_color} {ipo['company_name']} — {status}", expanded=(status == 'Applied')):
                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.markdown("**Application Details**")
                        st.write(f"Applied: {ipo.get('application_date', '—')}")
                        st.write(f"Price Band: ₹{ipo.get('issue_price_low', '—')} – ₹{ipo.get('issue_price_high', '—')}")
                        st.write(f"Lot Size: {ipo.get('lot_size', '—')} shares")
                        st.write(f"Lots Applied: {ipo.get('lots_applied', '—')}")
                        total_shares_applied = (ipo.get('lot_size') or 0) * (ipo.get('lots_applied') or 0)
                        total_amount = total_shares_applied * (ipo.get('issue_price_high') or 0)
                        st.write(f"Shares Applied: {total_shares_applied}")
                        st.write(f"Amount Blocked: {fmt_inr(total_amount)}")
                        st.write(f"UPI Mandate: {ipo.get('upi_mandate_status', '—')}")

                    with col2:
                        st.markdown("**Allotment Details**")
                        if status in ['Allotted', 'Listed']:
                            st.write(f"Allotment Date: {ipo.get('allotment_date', '—')}")
                            st.write(f"Lots Allotted: {ipo.get('lots_allotted', 0)}")
                            st.write(f"Shares Allotted: {ipo.get('shares_allotted', 0)}")
                            st.write(f"Allotment Price: ₹{ipo.get('allotment_price', '—')}")
                            allotted_cost = (ipo.get('shares_allotted') or 0) * (ipo.get('allotment_price') or 0)
                            st.write(f"Total Cost: {fmt_inr(allotted_cost)}")
                        elif status == 'Not Allotted':
                            st.write("❌ Not allotted — amount refunded")
                        else:
                            st.write("⏳ Awaiting allotment result")

                    with col3:
                        st.markdown("**Listing & P&L**")
                        if status == 'Listed':
                            listing_price = ipo.get('listing_price') or 0
                            allotment_price = ipo.get('allotment_price') or 0
                            shares = ipo.get('shares_allotted') or 0
                            listing_gain = (listing_price - allotment_price) * shares
                            listing_gain_pct = ((listing_price - allotment_price) / allotment_price * 100) if allotment_price else 0
                            st.write(f"Listing Date: {ipo.get('listing_date', '—')}")
                            st.write(f"Listing Price: ₹{listing_price:,.2f}")
                            gain_color = "🟢" if listing_gain >= 0 else "🔴"
                            st.write(f"Listing Gain: {gain_color} {fmt_inr(listing_gain)} ({listing_gain_pct:+.2f}%)")

                            # Live CMP
                            symbol = ipo.get('stock_symbol')
                            if symbol:
                                price_data = fetch_live_price(symbol)
                                if price_data:
                                    cmp = price_data['price']
                                    current_gain = (cmp - allotment_price) * shares
                                    current_pct = ((cmp - allotment_price) / allotment_price * 100) if allotment_price else 0
                                    st.write(f"Current CMP: ₹{cmp:,.2f} ({price_data['change_pct']:+.2f}%)")
                                    st.write(f"Current Gain: {fmt_inr(current_gain)} ({current_pct:+.2f}%)")
                        else:
                            st.write("⏳ Not listed yet")

                    st.markdown("---")

                    # ── Action Buttons ────────────────────────────────────────
                    action_cols = st.columns(4)

                    # Update Allotment
                    if status == 'Applied':
                        with action_cols[0]:
                            with st.form(key=f"allot_{ipo['id']}"):
                                st.markdown("**Update Allotment**")
                                allot_date = st.date_input("Allotment Date", key=f"ad_{ipo['id']}")
                                lots_got = st.number_input("Lots Allotted", min_value=0, step=1, key=f"la_{ipo['id']}")
                                allot_price = st.number_input("Allotment Price (₹)", min_value=0.0, step=0.5,
                                                               value=float(ipo.get('issue_price_high') or 0),
                                                               key=f"ap_{ipo['id']}")
                                if st.form_submit_button("Save Allotment"):
                                    shares_got = lots_got * (ipo.get('lot_size') or 0)
                                    new_status = 'Allotted' if lots_got > 0 else 'Not Allotted'
                                    db_update('ipo_tracker', {
                                        'allotment_date': allot_date.isoformat(),
                                        'lots_allotted': lots_got,
                                        'shares_allotted': shares_got,
                                        'allotment_price': allot_price,
                                        'status': new_status,
                                    }, 'id', ipo['id'])
                                    st.success(f"Allotment updated — {new_status}!")
                                    st.rerun()

                    # Update Listing
                    if status == 'Allotted':
                        with action_cols[1]:
                            with st.form(key=f"list_{ipo['id']}"):
                                st.markdown("**Update Listing**")
                                list_date = st.date_input("Listing Date", key=f"ld_{ipo['id']}")
                                list_price = st.number_input("Listing Price (₹)", min_value=0.0, step=0.5, key=f"lp_{ipo['id']}")
                                if st.form_submit_button("Save Listing"):
                                    db_update('ipo_tracker', {
                                        'listing_date': list_date.isoformat(),
                                        'listing_price': list_price,
                                        'status': 'Listed',
                                    }, 'id', ipo['id'])
                                    st.success("Listing details saved!")
                                    st.rerun()

                    # Push to Transactions
                    if status in ['Allotted', 'Listed'] and not ipo.get('pushed_to_transactions'):
                        with action_cols[2]:
                            st.markdown("**Push to Portfolio**")
                            # Require NSE symbol before pushing
                            if not ipo.get('stock_symbol'):
                                nse_sym = st.text_input("NSE Symbol required", 
                                    placeholder="e.g. HORIZONIND",
                                    key=f"sym_{ipo['id']}")
                                if nse_sym and st.button("Save Symbol", key=f"savesym_{ipo['id']}"):
                                    db_update('ipo_tracker', {'stock_symbol': nse_sym.upper()}, 'id', ipo['id'])
                                    # Also fix holdings and transactions if already pushed
                                    st.rerun()
                            elif st.button(f"➡️ Add to Transactions", key=f"push_{ipo['id']}"):
                                shares = ipo.get('shares_allotted') or 0
                                price = ipo.get('allotment_price') or 0
                                symbol = ipo.get('stock_symbol')
                                if shares > 0 and price > 0:
                                    charges = calculate_charges('BUY', shares, price)
                                    record = {
                                        'date': ipo.get('allotment_date') or date.today().isoformat(),
                                        'stock': symbol.upper(),
                                        'exchange': ipo.get('exchange', 'NSE'),
                                        'action': 'BUY',
                                        'qty': shares,
                                        'price': price,
                                        'notes': f"IPO Allotment — {ipo['company_name']}",
                                        **charges
                                    }
                                    db_insert('transactions', record)
                                    existing = db_select_eq('holdings', 'stock', symbol.upper())
                                    if not existing:
                                        db_insert('holdings', {'stock': symbol.upper(), 'exchange': ipo.get('exchange', 'NSE')})
                                    db_update('ipo_tracker', {'pushed_to_transactions': True}, 'id', ipo['id'])
                                    invalidate_cache()
                                    st.success(f"✅ {shares} shares of {symbol} added to your portfolio!")
                                    st.rerun()
                                else:
                                    st.error("Shares or price missing.")
                    elif ipo.get('pushed_to_transactions'):
                        with action_cols[2]:
                            st.success("✅ In Portfolio")

                    # Delete IPO
                    with action_cols[3]:
                        st.markdown("**Remove**")
                        if st.button("🗑️ Delete", key=f"del_ipo_{ipo['id']}"):
                            db_delete('ipo_tracker', 'id', ipo['id'])
                            st.success("Deleted.")
                            st.rerun()

    # ── TAB 2: ADD IPO APPLICATION ────────────────────────────────────────────
    with tab2:
        st.subheader("New IPO Application")

        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input("Company Name", placeholder="e.g. Ola Electric Mobility")
            stock_symbol = st.text_input("NSE Symbol (if known)", placeholder="e.g. OLAELEC").upper().strip()
            exchange = st.selectbox("Exchange", ["NSE", "BSE"])
            application_date = st.date_input("Application Date", value=date.today())
            upi_status = st.selectbox("UPI Mandate Status", ["Pending", "Approved", "Failed"])
            notes = st.text_input("Notes", placeholder="e.g. Applied via ICICI Direct")

        with col2:
            price_low = st.number_input("Price Band Low (₹)", min_value=0.0, step=0.5)
            price_high = st.number_input("Price Band High (₹)", min_value=0.0, step=0.5)
            lot_size = st.number_input("Lot Size (shares per lot)", min_value=1, step=1)
            lots_applied = st.number_input("Lots Applied", min_value=1, step=1, value=1)

            total_shares = lot_size * lots_applied
            total_amount = total_shares * price_high
            st.markdown(f"""
| | |
|---|---|
| Total Shares Applied | {total_shares} |
| Amount Blocked (UPI) | {fmt_inr(total_amount)} |
""")

        if st.button("✅ Save IPO Application", type="primary", use_container_width=True):
            if not company_name:
                st.error("Please enter company name.")
            else:
                record = {
                    'company_name': company_name,
                    'stock_symbol': stock_symbol or None,
                    'exchange': exchange,
                    'issue_price_low': price_low or None,
                    'issue_price_high': price_high or None,
                    'lot_size': lot_size,
                    'lots_applied': lots_applied,
                    'application_date': application_date.isoformat(),
                    'upi_mandate_status': upi_status,
                    'status': 'Applied',
                    'notes': notes or None,
                }
                db_insert('ipo_tracker', record)
                st.success(f"✅ IPO application for {company_name} saved!")
                st.balloons()
