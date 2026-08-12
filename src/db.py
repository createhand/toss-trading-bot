"""PostgreSQL 연동 — 포지션, 로그, 상태 관리"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, date
from typing import Generator

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

from .models import Signal, Side, OrderType, Holding


@dataclass
class DbConfig:
    host: str = "192.168.29.200"
    port: int = 5432
    database: str = "mysvc"
    user: str = "postgres"
    password: str = ""


class TradingDB:
    """트레이딩 봇 DB 매니저"""

    def __init__(self, config: DbConfig):
        self.config = config
        self._pool_config = dict(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=config.password,
        )

    @contextmanager
    def connect(self) -> Generator[PgConnection, None, None]:
        """커넥션 컨텍스트 매니저"""
        conn = psycopg2.connect(**self._pool_config)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """스키마 초기화 (테이블 없으면 생성)"""
        schema_path = "db/schema.sql"
        from pathlib import Path
        if Path(schema_path).exists():
            with open(schema_path, encoding="utf-8") as f:
                sql = f.read()
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql)
        else:
            # 파일이 없으면 직접 생성
            self._create_tables()

    def _create_tables(self) -> None:
        """필요한 테이블 생성"""
        sqls = [
            """
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
            )
            """,
            """
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
            )
            """,
            """
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
            )
            """,
            """
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
            )
            """,
        ]
        with self.connect() as conn:
            cur = conn.cursor()
            for sql in sqls:
                cur.execute(sql)

    # ── 포지션 ─────────────────────────────────────────

    def get_positions(self, symbol: str | None = None, status: str = "OPEN") -> list[dict]:
        """포지션 조회"""
        with self.connect() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            sql = "SELECT * FROM trading_positions WHERE status = %s"
            params: list = [status]
            if symbol:
                sql += " AND symbol = %s"
                params.append(symbol)
            sql += " ORDER BY symbol, slot"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def upsert_position(self, symbol: str, slot: int, entry_price: int,
                       quantity: int, target_price: int) -> int:
        """포지션 생성 (이미 있으면 업데이트)"""
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trading_positions (symbol, slot, entry_price, quantity, target_price, entry_time, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'OPEN')
                ON CONFLICT (symbol, slot)
                DO UPDATE SET
                    entry_price = EXCLUDED.entry_price,
                    quantity = EXCLUDED.quantity,
                    target_price = EXCLUDED.target_price,
                    entry_time = EXCLUDED.entry_time,
                    status = 'OPEN',
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
            """, (symbol, slot, entry_price, quantity, target_price, datetime.now()))
            return cur.fetchone()[0]

    def close_position(self, symbol: str, slot: int, close_price: int,
                       reason: str) -> None:
        """포지션 청산"""
        with self.connect() as conn:
            cur = conn.cursor()
            # 진입가 조회
            cur.execute(
                "SELECT entry_price, quantity FROM trading_positions WHERE symbol = %s AND slot = %s AND status = 'OPEN'",
                (symbol, slot),
            )
            row = cur.fetchone()
            if not row:
                return

            entry_price, quantity = row[0], row[1]
            pnl = (close_price - entry_price) * quantity
            pnl_pct = round((close_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0

            cur.execute("""
                UPDATE trading_positions SET
                    status = 'CLOSED',
                    close_time = CURRENT_TIMESTAMP,
                    close_price = %s,
                    close_reason = %s,
                    profit_loss = %s,
                    profit_loss_pct = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE symbol = %s AND slot = %s AND status = 'OPEN'
            """, (close_price, reason, pnl, pnl_pct, symbol, slot))

            # 봇 상태 PNL 업데이트
            self._update_daily_pnl(conn, pnl)

    def _update_daily_pnl(self, conn: PgConnection, pnl: int) -> None:
        cur = conn.cursor()
        cur.execute("""
            UPDATE trading_bot_status SET
                daily_pnl = daily_pnl + %s,
                total_pnl = total_pnl + %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (pnl, pnl))

    def has_open_position(self, symbol: str) -> bool:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM trading_positions WHERE symbol = %s AND status = 'OPEN'",
                (symbol,),
            )
            return cur.fetchone()[0] > 0

    def get_next_slot(self, symbol: str) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT slot FROM trading_positions WHERE symbol = %s ORDER BY slot",
                (symbol,),
            )
            used = {row[0] for row in cur.fetchall()}
            for i in range(1, 5):
                if i not in used:
                    return i
            return 0

    def get_first_entry_price(self, symbol: str) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT MIN(entry_price) FROM trading_positions WHERE symbol = %s",
                (symbol,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else 0

    # ── 스캔 로그 ───────────────────────────────────────

    def insert_scan_log(self, scan_date: date, symbol: str, name: str,
                        current_price: int, change_pct: float,
                        volume_ratio: float, volume_rank: float,
                        renko_trend: int, is_picked: bool = False) -> None:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trading_scan_log
                    (scan_date, symbol, name, current_price, change_pct, volume_ratio, volume_rank, renko_trend, is_picked)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (scan_date, symbol, name, current_price, change_pct,
                  volume_ratio, volume_rank, renko_trend, is_picked))

    def get_scan_log(self, scan_date: date | None = None, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if scan_date:
                cur.execute("""
                    SELECT * FROM trading_scan_log
                    WHERE scan_date = %s ORDER BY created_at DESC LIMIT %s
                """, (scan_date, limit))
            else:
                cur.execute("""
                    SELECT * FROM trading_scan_log
                    ORDER BY created_at DESC LIMIT %s
                """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ── 거래 로그 ───────────────────────────────────────

    def insert_trade_log(self, symbol: str, side: str, order_type: str,
                        quantity: int, price: int | None, order_id: str | None,
                        status: str, reason: str, dry_run: bool = True) -> None:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trading_trade_log
                    (symbol, side, order_type, quantity, price, order_id, status, reason, dry_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (symbol, side, order_type, quantity, price, order_id, status, reason, dry_run))

    def get_trade_log(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM trading_trade_log
                ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ── 봇 상태 ─────────────────────────────────────────

    def get_bot_status(self) -> dict:
        with self.connect() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM trading_bot_status WHERE id = 1")
            row = cur.fetchone()
            if row:
                return dict(row)
            # 초기 레코드 생성
            cur.execute("""
                INSERT INTO trading_bot_status (id, status, dry_run) VALUES (1, 'STOPPED', TRUE)
                ON CONFLICT DO NOTHING
            """)
            cur.execute("SELECT * FROM trading_bot_status WHERE id = 1")
            return dict(cur.fetchone())

    def update_bot_status(self, **kwargs) -> None:
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values())
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                UPDATE trading_bot_status SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
            """, values)

    def reset_daily_pnl(self) -> None:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE trading_bot_status SET daily_pnl = 0, updated_at = CURRENT_TIMESTAMP WHERE id = 1")

    # ── 요약 통계 ───────────────────────────────────────

    def get_summary(self) -> dict:
        """대시보드용 요약 데이터"""
        with self.connect() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # 오픈 포지션
            cur.execute("SELECT COUNT(*) as cnt FROM trading_positions WHERE status = 'OPEN'")
            open_positions = cur.fetchone()["cnt"]

            # 종료 포지션
            cur.execute("SELECT COUNT(*) as cnt FROM trading_positions WHERE status = 'CLOSED'")
            closed_positions = cur.fetchone()["cnt"]

            # 총 PnL
            cur.execute("""
                SELECT COALESCE(SUM(profit_loss), 0) as total_pnl
                FROM trading_positions WHERE status = 'CLOSED'
            """)
            total_pnl = cur.fetchone()["total_pnl"]

            # 승률
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE profit_loss > 0) as wins,
                       COUNT(*) as total
                FROM trading_positions WHERE status = 'CLOSED'
            """)
            row = cur.fetchone()
            win_rate = round(row["wins"] / row["total"] * 100, 1) if row["total"] > 0 else 0

            # 오늘 PnL
            cur.execute("""
                SELECT COALESCE(SUM(profit_loss), 0) as daily_pnl
                FROM trading_positions
                WHERE status = 'CLOSED' AND close_time::date = CURRENT_DATE
            """)
            daily_pnl = cur.fetchone()["daily_pnl"]

            return {
                "open_positions": open_positions,
                "closed_positions": closed_positions,
                "total_pnl": total_pnl,
                "win_rate": win_rate,
                "daily_pnl": daily_pnl,
            }
