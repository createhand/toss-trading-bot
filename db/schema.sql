-- 토스 트레이딩 봇 스키마
-- 기존 DB(mysvc)에 추가 생성

-- 포지션 (1계좌 4슬롯 분할 물타기)
CREATE TABLE IF NOT EXISTS trading_positions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    slot SMALLINT NOT NULL CHECK (slot BETWEEN 1 AND 4),
    entry_price INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    target_price INTEGER NOT NULL,
    stop_price INTEGER,
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN',
    entry_time TIMESTAMP NOT NULL,
    close_time TIMESTAMP,
    close_price INTEGER,
    close_reason VARCHAR(20),
    profit_loss INTEGER DEFAULT 0,
    profit_loss_pct DECIMAL(6,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol, slot)
);
CREATE UNIQUE INDEX IF NOT EXISTS trading_positions_symbol_slot_uniq ON trading_positions (symbol, slot);

-- 일일 스캔 로그 (후보 종목 기록)
CREATE TABLE IF NOT EXISTS trading_scan_log (
    id SERIAL PRIMARY KEY,
    scan_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    name VARCHAR(50),
    current_price INTEGER,
    change_pct DECIMAL(8,2),
    volume_ratio DECIMAL(10,2),
    volume_rank DECIMAL(5,4),
    renko_trend SMALLINT,
    is_picked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_scan_log_date ON trading_scan_log (scan_date DESC);

-- 거래 로그 (주문/체결 기록)
CREATE TABLE IF NOT EXISTS trading_trade_log (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(4) NOT NULL,
    order_type VARCHAR(6) NOT NULL,
    quantity INTEGER NOT NULL,
    price INTEGER,
    order_id VARCHAR(100),
    status VARCHAR(20) NOT NULL,
    reason TEXT,
    dry_run BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_trade_log_created ON trading_trade_log (created_at DESC);

-- 봇 상태
CREATE TABLE IF NOT EXISTS trading_bot_status (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    status VARCHAR(20) NOT NULL DEFAULT 'STOPPED',
    dry_run BOOLEAN DEFAULT TRUE,
    mode VARCHAR(30) DEFAULT 'loop',
    last_scan_time TIMESTAMP,
    last_trade_time TIMESTAMP,
    total_trades INTEGER DEFAULT 0,
    total_pnl INTEGER DEFAULT 0,
    daily_pnl INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 권한 (service_role이 있을 경우)
-- GRANT ALL ON public.trading_positions TO service_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.trading_positions TO authenticated;
-- GRANT ALL ON public.trading_scan_log TO service_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.trading_scan_log TO authenticated;
-- GRANT ALL ON public.trading_trade_log TO service_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.trading_trade_log TO authenticated;
-- GRANT ALL ON public.trading_bot_status TO service_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.trading_bot_status TO authenticated;
