-- ============================================
-- PORTFOLIO SCHEMA - Stock Tracker Application
-- Run this in Supabase SQL Editor
-- ============================================

CREATE SCHEMA IF NOT EXISTS portfolio;

-- ============================================
-- TRANSACTIONS TABLE
-- ============================================
CREATE TABLE portfolio.transactions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    date DATE NOT NULL,
    stock VARCHAR(50) NOT NULL,
    exchange VARCHAR(10) DEFAULT 'NSE',
    action VARCHAR(4) NOT NULL CHECK (action IN ('BUY', 'SELL')),
    qty INTEGER NOT NULL CHECK (qty > 0),
    price NUMERIC(12, 4) NOT NULL CHECK (price > 0),
    trade_value NUMERIC(14, 4),
    stt NUMERIC(10, 4),
    stamp_duty NUMERIC(10, 4),
    brokerage NUMERIC(10, 4),
    gst_on_brokerage NUMERIC(10, 4),
    sebi_charges NUMERIC(10, 4),
    total_charges NUMERIC(10, 4),
    landed_cost NUMERIC(14, 4),
    effective_unit_price NUMERIC(12, 4),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- HOLDINGS TABLE
-- ============================================
CREATE TABLE portfolio.holdings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    stock VARCHAR(50) NOT NULL UNIQUE,
    exchange VARCHAR(10) DEFAULT 'NSE',
    week_52_low NUMERIC(12, 4),
    week_52_high NUMERIC(12, 4),
    cmp NUMERIC(12, 4),
    cmp_updated_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- TAX SUMMARY CACHE TABLE (for FY summaries)
-- ============================================
CREATE TABLE portfolio.tax_summary (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    financial_year VARCHAR(10) NOT NULL,
    stock VARCHAR(50) NOT NULL,
    sell_date DATE NOT NULL,
    qty_sold INTEGER NOT NULL,
    buy_date DATE NOT NULL,
    buy_price NUMERIC(12, 4) NOT NULL,
    sell_price NUMERIC(12, 4) NOT NULL,
    holding_days INTEGER NOT NULL,
    gain_type VARCHAR(5) NOT NULL CHECK (gain_type IN ('STCG', 'LTCG')),
    buy_landed_cost NUMERIC(14, 4),
    sell_landed_cost NUMERIC(14, 4),
    gain_amount NUMERIC(14, 4),
    tax_rate NUMERIC(5, 2),
    estimated_tax NUMERIC(14, 4),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- INDEXES
-- ============================================
CREATE INDEX idx_transactions_stock ON portfolio.transactions(stock);
CREATE INDEX idx_transactions_date ON portfolio.transactions(date);
CREATE INDEX idx_transactions_action ON portfolio.transactions(action);
CREATE INDEX idx_tax_summary_fy ON portfolio.tax_summary(financial_year);
CREATE INDEX idx_tax_summary_stock ON portfolio.tax_summary(stock);

-- ============================================
-- AUTO UPDATE updated_at
-- ============================================
CREATE OR REPLACE FUNCTION portfolio.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_transactions_updated_at
    BEFORE UPDATE ON portfolio.transactions
    FOR EACH ROW EXECUTE FUNCTION portfolio.update_updated_at();

CREATE TRIGGER trg_holdings_updated_at
    BEFORE UPDATE ON portfolio.holdings
    FOR EACH ROW EXECUTE FUNCTION portfolio.update_updated_at();
