import streamlit as st
import pandas as pd
from datetime import date, datetime
from supabase_client import get_supabase_client
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

# ── Styling ──────────────────────────────────────────────────────────────────
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
    .stcg-badge { background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-size:0.75rem; font-weight:600; }
    .ltcg-badge { background:#dcfce7; color:#166534; padding:2px 8px; border-radius:4px; font-size:0.75rem; font-weight:600; }
    div[data-testid="stSidebarNav"] { display: none; }
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

def calculate_charges(action, qty, price, brokerage_per_order=20.0):
    trade_value = qty * price
    stt = trade_value * 0.001 if action == 'BUY' else trade_value * 0.00025
    stamp_duty = trade_value * 0.00015 if action == 'BUY' else 0.0
    brokerage = min(brokerage_per_order, trade_value * 0.0003)
    gst = brokerage * 0.18
    sebi = trade_value * 0.000001
    total_charges = stt + stamp_duty + brokerage + gst + sebi
    if action == 'BUY':
        landed_cost = trade_value + total_charges
    else:
        landed_cost = trade_value - total_charges
    effective_unit_price = landed_cost / qty
    return {
        'trade_value': round(trade_value, 4),
        'stt': round(stt, 4),
        'stamp_duty': round(stamp_duty, 4),
        'brokerage': round(brokerage, 4),
        'gst_on_brokerage': round(gst, 4),
        'sebi_charges': round(sebi, 4),
        'total_charges': round(total_charges, 4),
        'landed_cost': round(landed_cost, 4),
        'effective_unit_price': round(effective_unit_price, 4),
    }

# ── Data fetchers ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def fetch_transactions():
    db = get_supabase_client()
    res = db.schema('portfolio').table('transactions').select('*').order('date', desc=True).execute()
    return res.data or []

@st.cache_data(ttl=60)
def fetch_holdings_meta():
    db = get_supabase_client()
    res = db.schema('portfolio').table('holdings').select('*').execute()
    return {r['stock']: r for r in (res.data or [])}

def invalidate_cache():
    fetch_transactions.clear()
    fetch_holdings_meta.clear()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 Portfolio Tracker")
    st.markdown("---")
    page = st.radio(
        "Navigate",
        ["🏠 Dashboard", "➕ Add Transaction", "📋 Transactions", "💼 Holdings", "🧾 Tax Summary"],
        label_visibility="collapsed"
    )
    st.markdown("---")
    st.caption("Indian Equity | FIFO | Budget 2024 Rates")
    st.caption(f"STCG: {int(STCG_RATE*100)}%  |  LTCG: {int(LTCG_RATE*100)}%")
    st.caption(f"LTCG Exemption: ₹1.25L/FY")

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
        st.info("No open holdings found. All positions appear to be closed.")
        st.stop()

    # ── CMP Update Section ────────────────────────────────────────────────────
    with st.expander("📡 Update Current Market Prices (CMP)", expanded=False):
        db = get_supabase_client()
        cols = st.columns(min(len(holdings), 4))
        for i, (stock, h) in enumerate(holdings.items()):
            meta = holdings_meta.get(stock, {})
            with cols[i % len(cols)]:
                new_cmp = st.number_input(
                    f"{stock}",
                    value=float(meta.get('cmp') or h['avg_cost']),
                    step=0.05,
                    format="%.2f",
                    key=f"cmp_{stock}"
                )
                if st.button("Update", key=f"upd_{stock}"):
                    existing = db.schema('portfolio').table('holdings').select('id').eq('stock', stock).execute()
                    if existing.data:
                        db.schema('portfolio').table('holdings').update({
                            'cmp': new_cmp,
                            'cmp_updated_at': datetime.utcnow().isoformat()
                        }).eq('stock', stock).execute()
                    else:
                        db.schema('portfolio').table('holdings').insert({
                            'stock': stock,
                            'cmp': new_cmp,
                            'cmp_updated_at': datetime.utcnow().isoformat()
                        }).execute()
                    invalidate_cache()
                    st.success(f"CMP updated for {stock}")
                    st.rerun()

    # ── Portfolio Summary ─────────────────────────────────────────────────────
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
        target_25 = h['avg_cost'] * 1.25
        target_50 = h['avg_cost'] * 1.50
        target_100 = h['avg_cost'] * 2.00
        week_52_low = meta.get('week_52_low')
        week_52_high = meta.get('week_52_high')

        total_invested += invested
        total_current += current_val

        rows.append({
            'Stock': stock,
            'Qty': h['qty'],
            'Avg Cost': h['avg_cost'],
            'CMP': cmp,
            'Invested': invested,
            'Current Value': current_val,
            'P&L (₹)': pnl,
            'P&L (%)': pnl_pct,
            '25% Target': target_25,
            '50% Target': target_50,
            '100% Target': target_100,
            '52W Low': week_52_low,
            '52W High': week_52_high,
        })

    total_pnl = total_current - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0

    # ── Metric Cards ──────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    def metric_card(col, label, value, sub=None, sub_class="neutral"):
        col.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            {'<div class="metric-sub ' + sub_class + '">' + sub + '</div>' if sub else ''}
        </div>""", unsafe_allow_html=True)

    metric_card(c1, "Total Invested", fmt_inr(total_invested))
    metric_card(c2, "Current Value", fmt_inr(total_current))
    metric_card(c3, "Overall P&L", fmt_inr(total_pnl),
                f"{total_pnl_pct:+.2f}%", pnl_color(total_pnl))
    metric_card(c4, "Stocks Held", str(len(holdings)))

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Holdings Table ────────────────────────────────────────────────────────
    st.subheader("Holdings")
    df = pd.DataFrame(rows)

    def color_pnl(val):
        if isinstance(val, float):
            color = '#16a34a' if val >= 0 else '#dc2626'
            return f'color: {color}; font-weight: 600'
        return ''

    display_df = df.copy()
    for col in ['Avg Cost', 'CMP', '25% Target', '50% Target', '100% Target']:
        display_df[col] = display_df[col].apply(lambda x: f"₹{x:,.2f}")
    display_df['Invested'] = display_df['Invested'].apply(fmt_inr)
    display_df['Current Value'] = display_df['Current Value'].apply(fmt_inr)
    display_df['P&L (₹)'] = df['P&L (₹)'].apply(fmt_inr)
    display_df['P&L (%)'] = df['P&L (%)'].apply(lambda x: f"{x:+.2f}%")
    display_df['52W Low'] = display_df['52W Low'].apply(lambda x: f"₹{float(x):,.2f}" if x else "—")
    display_df['52W High'] = display_df['52W High'].apply(lambda x: f"₹{float(x):,.2f}" if x else "—")

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── Current FY Tax Snapshot ───────────────────────────────────────────────
    st.subheader("Current FY Tax Snapshot")
    current_fy = get_financial_year(date.today())
    all_lots = compute_tax_lots(txns)
    fy_summary = compute_fy_tax_summary(all_lots)

    if current_fy in fy_summary:
        s = fy_summary[current_fy]
        t1, t2, t3, t4 = st.columns(4)
        metric_card(t1, f"STCG ({current_fy})", fmt_inr(s['stcg_gain']),
                    f"Tax: {fmt_inr(s['stcg_tax'])}", pnl_color(s['stcg_gain']))
        metric_card(t2, f"LTCG ({current_fy})", fmt_inr(s['ltcg_gain']),
                    f"Tax: {fmt_inr(s['ltcg_tax'])}", pnl_color(s['ltcg_gain']))
        metric_card(t3, "LTCG Exempt", fmt_inr(s['ltcg_exempt']), "₹1.25L/FY limit", "neutral")
        metric_card(t4, "Est. Total Tax", fmt_inr(s['total_tax']), current_fy, pnl_color(-s['total_tax']))
    else:
        st.info(f"No realised gains in {current_fy} yet.")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: ADD TRANSACTION
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "➕ Add Transaction":
    st.title("➕ Add Transaction")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Transaction Details")
        txn_date = st.date_input("Date", value=date.today(), max_value=date.today())
        stock = st.text_input("Stock Symbol", placeholder="e.g. JIOFIN, SBFC, HDFCBANK").upper().strip()
        exchange = st.selectbox("Exchange", ["NSE", "BSE"])
        action = st.radio("Action", ["BUY", "SELL"], horizontal=True)
        qty = st.number_input("Quantity (shares)", min_value=1, step=1, value=1)
        price = st.number_input("Price per share (₹)", min_value=0.01, step=0.05, format="%.4f")
        brokerage_input = st.number_input("Brokerage per order (₹)", value=20.0, step=1.0)
        notes = st.text_input("Notes (optional)", placeholder="e.g. Added on dip")

    with col2:
        st.subheader("Charge Breakdown")
        if stock and qty > 0 and price > 0:
            charges = calculate_charges(action, qty, price, brokerage_input)
            st.markdown(f"""
| Component | Amount |
|---|---|
| Trade Value | ₹{charges['trade_value']:,.4f} |
| STT | ₹{charges['stt']:,.4f} |
| Stamp Duty | ₹{charges['stamp_duty']:,.4f} |
| Brokerage | ₹{charges['brokerage']:,.4f} |
| GST on Brokerage | ₹{charges['gst_on_brokerage']:,.4f} |
| SEBI Charges | ₹{charges['sebi_charges']:,.4f} |
| **Total Charges** | **₹{charges['total_charges']:,.4f}** |
| **Landed Cost** | **₹{charges['landed_cost']:,.4f}** |
| **Eff. Unit Price** | **₹{charges['effective_unit_price']:,.4f}** |
""")

            st.markdown(f"**Financial Year:** {get_financial_year(txn_date)}")

            if st.button("✅ Save Transaction", type="primary", use_container_width=True):
                if not stock:
                    st.error("Please enter a stock symbol.")
                else:
                    db = get_supabase_client()
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
                    db.schema('portfolio').table('transactions').insert(record).execute()

                    # ensure holdings meta row exists
                    existing = db.schema('portfolio').table('holdings').select('id').eq('stock', stock).execute()
                    if not existing.data:
                        db.schema('portfolio').table('holdings').insert({
                            'stock': stock,
                            'exchange': exchange,
                        }).execute()

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

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        stocks_list = ['All'] + sorted(df['stock'].unique().tolist())
        sel_stock = st.selectbox("Filter by Stock", stocks_list)
    with col2:
        sel_action = st.selectbox("Filter by Action", ['All', 'BUY', 'SELL'])
    with col3:
        years = sorted(df['date'].dt.year.unique().tolist(), reverse=True)
        sel_year = st.selectbox("Filter by Year", ['All'] + [str(y) for y in years])

    filtered = df.copy()
    if sel_stock != 'All':
        filtered = filtered[filtered['stock'] == sel_stock]
    if sel_action != 'All':
        filtered = filtered[filtered['action'] == sel_action]
    if sel_year != 'All':
        filtered = filtered[filtered['date'].dt.year == int(sel_year)]

    filtered = filtered.sort_values('date', ascending=False)

    display_cols = ['date', 'stock', 'exchange', 'action', 'qty', 'price',
                    'trade_value', 'stt', 'stamp_duty', 'brokerage',
                    'total_charges', 'landed_cost', 'effective_unit_price', 'notes']
    display_cols = [c for c in display_cols if c in filtered.columns]

    display = filtered[display_cols].copy()
    display['date'] = display['date'].dt.strftime('%d-%b-%Y')

    def highlight_action(row):
        if row.get('action') == 'BUY':
            return ['background-color: #f0fdf4'] * len(row)
        elif row.get('action') == 'SELL':
            return ['background-color: #fff1f2'] * len(row)
        return [''] * len(row)

    styled = display.style.apply(highlight_action, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

    st.caption(f"Showing {len(filtered)} of {len(df)} transactions")

    # Delete transaction
    with st.expander("🗑️ Delete a Transaction"):
        st.warning("Select a transaction ID to delete. This cannot be undone.")
        del_id = st.selectbox("Transaction ID", [r['id'] for r in txns])
        if st.button("Delete", type="secondary"):
            db = get_supabase_client()
            db.schema('portfolio').table('transactions').delete().eq('id', del_id).execute()
            invalidate_cache()
            st.success("Transaction deleted.")
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

    # 52W Low/High update
    st.subheader("Update 52-Week Range")
    db = get_supabase_client()
    cols = st.columns(min(len(holdings), 3))
    for i, stock in enumerate(holdings):
        meta = holdings_meta.get(stock, {})
        with cols[i % 3]:
            with st.form(key=f"form_52w_{stock}"):
                st.markdown(f"**{stock}**")
                low = st.number_input("52W Low", value=float(meta.get('week_52_low') or 0), step=0.05, format="%.2f", key=f"low_{stock}")
                high = st.number_input("52W High", value=float(meta.get('week_52_high') or 0), step=0.05, format="%.2f", key=f"high_{stock}")
                if st.form_submit_button("Save"):
                    existing = db.schema('portfolio').table('holdings').select('id').eq('stock', stock).execute()
                    if existing.data:
                        db.schema('portfolio').table('holdings').update({
                            'week_52_low': low,
                            'week_52_high': high
                        }).eq('stock', stock).execute()
                    invalidate_cache()
                    st.success(f"Saved for {stock}")
                    st.rerun()

    st.markdown("---")
    st.subheader("Holdings Summary")

    holdings_meta = fetch_holdings_meta()
    rows = []
    for stock, h in holdings.items():
        meta = holdings_meta.get(stock, {})
        cmp = float(meta.get('cmp') or h['avg_cost'])
        current_val = h['qty'] * cmp
        pnl = current_val - h['total_cost']
        pnl_pct = (pnl / h['total_cost'] * 100) if h['total_cost'] else 0
        rows.append({
            'Stock': stock,
            'Exchange': meta.get('exchange', 'NSE'),
            'Qty': h['qty'],
            'Avg Cost (₹)': round(h['avg_cost'], 2),
            'CMP (₹)': round(cmp, 2),
            'Purchase Cost (₹)': round(h['total_cost'], 2),
            'Current Value (₹)': round(current_val, 2),
            'P&L (₹)': round(pnl, 2),
            'P&L (%)': round(pnl_pct, 2),
            '52W Low (₹)': round(float(meta.get('week_52_low') or 0), 2) or None,
            '52W High (₹)': round(float(meta.get('week_52_high') or 0), 2) or None,
            '25% Target (₹)': round(h['avg_cost'] * 1.25, 2),
            '50% Target (₹)': round(h['avg_cost'] * 1.50, 2),
            '100% Target (₹)': round(h['avg_cost'] * 2.00, 2),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: TAX SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🧾 Tax Summary":
    st.title("🧾 STCG / LTCG Tax Summary")
    st.caption("FIFO method · Budget 2024 rates · STCG 20% · LTCG 12.5% · Exemption ₹1.25L/FY")

    txns = fetch_transactions()

    if not txns:
        st.info("No transactions yet.")
        st.stop()

    all_lots = compute_tax_lots(txns)

    if not all_lots:
        st.info("No realised gains/losses yet. Add sell transactions to see tax summary.")
        st.stop()

    fy_summary = compute_fy_tax_summary(all_lots)

    # FY Selector
    fy_list = sorted(fy_summary.keys(), reverse=True)
    sel_fy = st.selectbox("Select Financial Year", fy_list)

    s = fy_summary[sel_fy]

    # Summary cards
    c1, c2, c3, c4, c5 = st.columns(5)
    def tax_card(col, label, value, sub=None, cls="neutral"):
        col.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            {'<div class="metric-sub ' + cls + '">' + sub + '</div>' if sub else ''}
        </div>""", unsafe_allow_html=True)

    tax_card(c1, "STCG Gain", fmt_inr(s['stcg_gain']), f"@{s['stcg_rate']}% = {fmt_inr(s['stcg_tax'])}", pnl_color(s['stcg_gain']))
    tax_card(c2, "LTCG Gain", fmt_inr(s['ltcg_gain']), f"@{s['ltcg_rate']}% = {fmt_inr(s['ltcg_tax'])}", pnl_color(s['ltcg_gain']))
    tax_card(c3, "LTCG Exempt", fmt_inr(s['ltcg_exempt']), "₹1.25L limit", "neutral")
    tax_card(c4, "LTCG Taxable", fmt_inr(s['ltcg_taxable']), "After exemption", pnl_color(s['ltcg_taxable']))
    tax_card(c5, "Est. Tax Liability", fmt_inr(s['total_tax']), sel_fy, "loss" if s['total_tax'] > 0 else "neutral")

    st.markdown("<br>", unsafe_allow_html=True)

    # Lot-wise detail
    st.subheader("Lot-wise Breakdown")
    lots_df = pd.DataFrame([l for l in all_lots if l['financial_year'] == sel_fy])

    if not lots_df.empty:
        lots_df['sell_date'] = pd.to_datetime(lots_df['sell_date']).dt.strftime('%d-%b-%Y')
        lots_df['buy_date'] = pd.to_datetime(lots_df['buy_date']).dt.strftime('%d-%b-%Y')
        lots_df['gain_type_badge'] = lots_df['gain_type']

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
            if val == 'STCG': return 'background-color: #fef3c7; color: #92400e; font-weight: 600'
            if val == 'LTCG': return 'background-color: #dcfce7; color: #166534; font-weight: 600'
            return ''

        def color_gain(val):
            if isinstance(val, (int, float)):
                return f"color: {'#16a34a' if val >= 0 else '#dc2626'}; font-weight: 600"
            return ''

        styled = display_lots.style\
            .applymap(color_type, subset=['Type'])\
            .applymap(color_gain, subset=['Gain/Loss (₹)'])

        st.dataframe(styled, use_container_width=True, hide_index=True)

    # All FY Summary Table
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
            'Total Gain (₹)': round(s['total_gain'], 2),
        })

    fy_df = pd.DataFrame(fy_rows)
    st.dataframe(fy_df, use_container_width=True, hide_index=True)
