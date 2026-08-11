"""
Indian Equity Tax Engine
- FIFO method for lot matching
- STCG: sold within 12 months -> 20% (Budget 2024)
- LTCG: sold after 12 months -> 12.5% above Rs 1.25 lakh exemption (Budget 2024)
- Financial Year: April to March
"""

from datetime import date
from typing import List, Dict, Any
import pandas as pd


STCG_RATE = 0.20      # 20% flat
LTCG_RATE = 0.125     # 12.5%
LTCG_EXEMPTION = 125000  # Rs 1.25 lakh per FY


def get_financial_year(dt: date) -> str:
    if dt.month >= 4:
        return f"FY{str(dt.year)[2:]}-{str(dt.year + 1)[2:]}"
    else:
        return f"FY{str(dt.year - 1)[2:]}-{str(dt.year)[2:]}"


def get_fy_start_end(fy: str) -> tuple:
    parts = fy.replace("FY", "").split("-")
    start_year = int("20" + parts[0])
    end_year = int("20" + parts[1])
    return date(start_year, 4, 1), date(end_year, 3, 31)


def compute_tax_lots(transactions: List[Dict]) -> List[Dict]:
    """
    Process all transactions using FIFO to compute STCG/LTCG for each sell.
    Returns list of matched lot records.
    """
    if not transactions:
        return []

    df = pd.DataFrame(transactions)
    df['date'] = pd.to_datetime(df['date']).dt.date
    df = df.sort_values('date').reset_index(drop=True)

    stocks = df['stock'].unique()
    all_lots = []

    for stock in stocks:
        stock_df = df[df['stock'] == stock].copy()
        buy_queue = []  # list of dicts: {date, qty, price, landed_cost}

        for _, row in stock_df.iterrows():
            if row['action'] == 'BUY':
                buy_queue.append({
                    'date': row['date'],
                    'qty': row['qty'],
                    'price': float(row['effective_unit_price'] or row['price']),
                    'landed_cost_per_unit': float(row['landed_cost'] / row['qty']) if row['landed_cost'] else float(row['price'])
                })
            elif row['action'] == 'SELL':
                sell_qty = row['qty']
                sell_price = float(row['price'])
                sell_landed_per_unit = float(row['landed_cost'] / row['qty']) if row['landed_cost'] else sell_price
                sell_date = row['date']

                remaining_sell = sell_qty

                while remaining_sell > 0 and buy_queue:
                    buy_lot = buy_queue[0]

                    matched_qty = min(remaining_sell, buy_lot['qty'])
                    holding_days = (sell_date - buy_lot['date']).days
                    gain_type = 'LTCG' if holding_days > 365 else 'STCG'

                    buy_cost = buy_lot['landed_cost_per_unit'] * matched_qty
                    sell_proceeds = sell_price * matched_qty
                    gain = sell_proceeds - buy_cost

                    tax_rate = LTCG_RATE if gain_type == 'LTCG' else STCG_RATE

                    all_lots.append({
                        'financial_year': get_financial_year(sell_date),
                        'stock': stock,
                        'sell_date': sell_date,
                        'buy_date': buy_lot['date'],
                        'qty_sold': matched_qty,
                        'buy_price': buy_lot['price'],
                        'sell_price': sell_price,
                        'holding_days': holding_days,
                        'gain_type': gain_type,
                        'buy_landed_cost': buy_cost,
                        'sell_proceeds': sell_proceeds,
                        'gain_amount': gain,
                        'tax_rate': tax_rate * 100,
                    })

                    buy_lot['qty'] -= matched_qty
                    remaining_sell -= matched_qty

                    if buy_lot['qty'] == 0:
                        buy_queue.pop(0)

    return all_lots


def compute_fy_tax_summary(lots: List[Dict]) -> Dict[str, Any]:
    """
    Aggregate lots by FY and compute tax with LTCG exemption applied.
    Returns dict keyed by FY.
    """
    if not lots:
        return {}

    df = pd.DataFrame(lots)
    summary = {}

    for fy in df['financial_year'].unique():
        fy_df = df[df['financial_year'] == fy]

        stcg_df = fy_df[fy_df['gain_type'] == 'STCG']
        ltcg_df = fy_df[fy_df['gain_type'] == 'LTCG']

        stcg_gain = float(stcg_df['gain_amount'].sum())
        ltcg_gain = float(ltcg_df['gain_amount'].sum())

        # LTCG exemption Rs 1.25 lakh per FY
        ltcg_taxable = max(0, ltcg_gain - LTCG_EXEMPTION)

        stcg_tax = max(0, stcg_gain) * STCG_RATE
        ltcg_tax = ltcg_taxable * LTCG_RATE

        summary[fy] = {
            'financial_year': fy,
            'stcg_gain': stcg_gain,
            'ltcg_gain': ltcg_gain,
            'ltcg_exempt': min(ltcg_gain, LTCG_EXEMPTION) if ltcg_gain > 0 else 0,
            'ltcg_taxable': ltcg_taxable,
            'stcg_tax': stcg_tax,
            'ltcg_tax': ltcg_tax,
            'total_tax': stcg_tax + ltcg_tax,
            'total_gain': stcg_gain + ltcg_gain,
            'stcg_rate': STCG_RATE * 100,
            'ltcg_rate': LTCG_RATE * 100,
            'ltcg_exemption': LTCG_EXEMPTION,
        }

    return summary


def compute_holdings_from_transactions(transactions: List[Dict]) -> Dict[str, Dict]:
    """
    Compute current holdings using FIFO.
    Returns dict keyed by stock with qty and avg cost.
    """
    if not transactions:
        return {}

    df = pd.DataFrame(transactions)
    df['date'] = pd.to_datetime(df['date']).dt.date
    df = df.sort_values('date').reset_index(drop=True)

    stocks = df['stock'].unique()
    holdings = {}

    for stock in stocks:
        stock_df = df[df['stock'] == stock].copy()
        buy_queue = []

        for _, row in stock_df.iterrows():
            if row['action'] == 'BUY':
                buy_queue.append({
                    'date': row['date'],
                    'qty': row['qty'],
                    'landed_cost_per_unit': float(row['landed_cost'] / row['qty']) if row.get('landed_cost') and row['qty'] else float(row['price'])
                })
            elif row['action'] == 'SELL':
                remaining = row['qty']
                while remaining > 0 and buy_queue:
                    lot = buy_queue[0]
                    matched = min(remaining, lot['qty'])
                    lot['qty'] -= matched
                    remaining -= matched
                    if lot['qty'] == 0:
                        buy_queue.pop(0)

        total_qty = sum(lot['qty'] for lot in buy_queue)
        if total_qty > 0:
            total_cost = sum(lot['qty'] * lot['landed_cost_per_unit'] for lot in buy_queue)
            avg_cost = total_cost / total_qty
            holdings[stock] = {
                'stock': stock,
                'qty': total_qty,
                'avg_cost': avg_cost,
                'total_cost': total_cost,
                'lots': buy_queue
            }

    return holdings
