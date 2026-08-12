"""리스크 관리 — 포지션 사이즈, 한도, 일일 손실 제한"""

from __future__ import annotations

from dataclasses import dataclass

from .config import RiskConfig
from .models import Signal, Holding, Side
from .notify import NotifyLogger
from .utils import fmt_num


@dataclass
class RiskCheckResult:
    """리스크 체크 결과"""
    passed: bool
    signal: Signal | None = None
    reason: str = ""


class RiskManager:
    """리스크 관리자

    모든 주문 전에 리스크 규칙을 검사하여 위험한 주문을 차단합니다.
    """

    def __init__(self, config: RiskConfig, logger: NotifyLogger):
        self.config = config
        self.logger = logger
        # 일일 손실 추적 (실제로는 체결 후 업데이트 필요)
        self._daily_realized_pnl: int = 0
        self._daily_start_value: int = 0

    def reset_daily(self, start_value: int) -> None:
        """영업일 시작 시 초기화"""
        self._daily_realized_pnl = 0
        self._daily_start_value = start_value

    def check(self, signal: Signal, holdings: list[Holding],
              total_assets: int, buying_power: int) -> RiskCheckResult:
        """시그널이 리스크 규칙을 통과하는지 검사"""

        # 1. 일일 손실 한도 체크
        if self._daily_start_value > 0:
            loss_rate = abs(self._daily_realized_pnl) / self._daily_start_value * 100
            if self._daily_realized_pnl < 0 and loss_rate >= self.config.daily_loss_limit_pct:
                return RiskCheckResult(
                    passed=False, signal=signal,
                    reason=f"일일 손실 한도 초과: {loss_rate:.1f}% / {self.config.daily_loss_limit_pct}%",
                )

        # 2. 총 포지션 한도 체크
        current_position_value = sum(
            h.quantity * h.current_price for h in holdings
        )
        order_value = signal.quantity * (signal.price or 0)
        new_position_pct = (current_position_value + order_value) / total_assets * 100 if total_assets > 0 else 0

        if signal.side == Side.BUY and new_position_pct > self.config.max_position_pct:
            return RiskCheckResult(
                passed=False, signal=signal,
                reason=f"총 포지션 한도 초과: {new_position_pct:.1f}% / {self.config.max_position_pct}%",
            )

        # 3. 단일 종목 한도 체크
        existing_qty = 0
        existing_avg = 0
        for h in holdings:
            if h.symbol == signal.symbol:
                existing_qty = h.quantity
                existing_avg = h.avg_price
                break

        if signal.side == Side.BUY:
            existing_value = existing_qty * existing_avg
            add_value = signal.quantity * (signal.price or 0)
            single_pct = (existing_value + add_value) / total_assets * 100 if total_assets > 0 else 0

            if single_pct > self.config.max_single_stock_pct:
                return RiskCheckResult(
                    passed=False, signal=signal,
                    reason=f"단일 종목 한도 초과: {single_pct:.1f}% / {self.config.max_single_stock_pct}%",
                )

        # 4. 매수 가능 금액 체크
        if signal.side == Side.BUY:
            required = signal.quantity * (signal.price or 0) if signal.order_type.value == "LIMIT" else buying_power
            if required > buying_power and signal.order_type.value != "MARKET":
                return RiskCheckResult(
                    passed=False, signal=signal,
                    reason=f"매수 가능 금액 부족: 필요 {fmt_num(required)}원 / 가능 {fmt_num(buying_power)}원",
                )

        return RiskCheckResult(passed=True, signal=signal)

    def record_pnl(self, pnl: int) -> None:
        """실현 손익 기록 (체결 후 호출)"""
        self._daily_realized_pnl += pnl
