"""알림 — stdout 로그 + webhook 확장 가능"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .utils import fmt_num, format_kst


@dataclass
class NotifyLogger:
    """알림 로거 (stdout + webhook 옵션)"""
    log_to_stdout: bool = True
    webhook_url: str | None = None
    dry_run: bool = True

    def info(self, msg: str) -> None:
        self._log("INFO", msg)

    def warn(self, msg: str) -> None:
        self._log("WARN", msg)

    def error(self, msg: str) -> None:
        self._log("ERROR", msg)

    def signal(self, signal: Any) -> None:
        """시그널 발생 알림"""
        prefix = "🔔" if not self.dry_run else "📝[DRY]"
        side = signal.side.value if hasattr(signal.side, "value") else signal.side
        self.info(
            f"{prefix} SIGNAL: {side} {signal.symbol} "
            f"x{signal.quantity} @ {fmt_num(signal.price) if signal.price else 'MARKET'} "
            f"— {signal.reason}"
        )

    def order(self, order_result: Any, dry_run: bool = False) -> None:
        """주문 결과 알림"""
        if dry_run:
            self.info(f"📝[DRY] 주문 건너뜀 (dry-run 모드): {order_result}")
        elif order_result.get("success"):
            self.info(f"✅ 주문 성공: {order_result.order_id} {order_result.symbol} {order_result.side}")
        else:
            self.error(f"❌ 주문 실패: {order_result.message}")

    def risk_block(self, reason: str) -> None:
        """리스크 차단 알림"""
        self.warn(f"🛑 리스크 차단: {reason}")

    def _log(self, level: str, msg: str) -> None:
        ts = format_kst()
        line = f"[{ts}] [{level}] {msg}"
        if self.log_to_stdout:
            print(line)

    def send_webhook(self, title: str, body: str) -> None:
        """Webhook 알림 (Slack, Discord 등)"""
        if not self.webhook_url:
            return
        try:
            import requests
            payload = {
                "title": title,
                "body": body,
                "timestamp": datetime.now().isoformat(),
            }
            requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
        except Exception as e:
            self.warn(f"Webhook 전송 실패: {e}")
