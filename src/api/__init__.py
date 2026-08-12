"""Flask REST API — 트레이딩 봇 현황 제공 + 제어

엔드포인트:
  GET  /api/status          — 봇 상태 + 요약
  GET  /api/positions       — 오픈 포지션 목록
  GET  /api/positions/all    — 전체 포지션 (종료 포함)
  GET  /api/scan-log        — 스캔 로그
  GET  /api/trade-log       — 거래 로그
  POST /api/control          — 봇 제어 (start/stop/dry-run 토글)
  GET  /api/health          — 헬스체크
"""

from __future__ import annotations

from flask import Flask, jsonify, request
from datetime import date

from .db import TradingDB


def create_app(db: TradingDB, engine=None) -> Flask:
    """Flask 앱 팩토리"""
    app = Flask(__name__)

    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/status")
    def get_status():
        bot_status = db.get_bot_status()
        summary = db.get_summary()
        return jsonify({
            "bot": bot_status,
            "summary": summary,
        })

    @app.route("/api/positions")
    def get_positions():
        symbol = request.args.get("symbol")
        positions = db.get_positions(symbol=symbol, status="OPEN")
        return jsonify({"positions": positions})

    @app.route("/api/positions/all")
    def get_all_positions():
        symbol = request.args.get("symbol")
        positions = db.get_positions(symbol=symbol, status=None) if symbol else []
        # status=None인 경우엔 별도 쿼리
        if symbol:
            from .db import TradingDB as _tdb
            with db.connect() as conn:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM trading_positions WHERE symbol = %s ORDER BY slot", (symbol,))
                positions = [dict(r) for r in cur.fetchall()]
        else:
            with db.connect() as conn:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM trading_positions ORDER BY symbol, slot")
                positions = [dict(r) for r in cur.fetchall()]
        return jsonify({"positions": positions})

    @app.route("/api/scan-log")
    def get_scan_log():
        scan_date_str = request.args.get("date")
        scan_date = date.fromisoformat(scan_date_str) if scan_date_str else None
        limit = request.args.get("limit", 50, type=int)
        logs = db.get_scan_log(scan_date=scan_date, limit=limit)
        return jsonify({"logs": logs})

    @app.route("/api/trade-log")
    def get_trade_log():
        limit = request.args.get("limit", 100, type=int)
        logs = db.get_trade_log(limit=limit)
        return jsonify({"logs": logs})

    @app.route("/api/control", methods=["POST"])
    def control():
        """봇 제어

        Body:
          action: "start" | "stop" | "toggle_dry_run"
        """
        data = request.get_json(silent=True) or {}
        action = data.get("action", "")

        if action == "start":
            db.update_bot_status(status="RUNNING")
            if engine:
                engine._running = True
            return jsonify({"message": "봇 시작", "status": "RUNNING"})

        elif action == "stop":
            db.update_bot_status(status="STOPPED")
            if engine:
                engine._running = False
            return jsonify({"message": "봇 정지", "status": "STOPPED"})

        elif action == "toggle_dry_run":
            current = db.get_bot_status()
            new_dry = not current.get("dry_run", True)
            db.update_bot_status(dry_run=new_dry)
            return jsonify({"message": f"Dry-run {'ON' if new_dry else 'OFF'}", "dry_run": new_dry})

        else:
            return jsonify({"error": f"알 수 없는 액션: {action}"}), 400

    return app
