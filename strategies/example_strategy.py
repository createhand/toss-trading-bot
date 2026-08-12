"""예시 전략 — 빈 껍데기 (참고용)

실제 전략 구현 시 이 파일을 참고하세요.
이 전략은 아무 시그널도 생성하지 않습니다.
"""

from src.strategy.base import BaseStrategy
from src.models import Signal, StrategyContext, Side, OrderType


class ExampleStrategy(BaseStrategy):
    """예시 전략 — 실제로는 아무 동작도 하지 않습니다"""

    def name(self) -> str:
        return "Example Strategy"

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """빈 시그널 반환 (아무 매매도 하지 않음)"""
        return []

        # ── 실제 전략 예시 (주석 처리) ──────────────────
        # signals = []
        #
        # for symbol, candles in context.candles.items():
        #     if len(candles) < 2:
        #         continue
        #
        #     # 최근 2일 캔들로 간단한 모멘텀 판단
        #     latest = candles[-1]
        #     prev = candles[-2]
        #     change_pct = (latest.close - prev.close) / prev.close * 100
        #
        #     price = context.prices.get(symbol)
        #     if not price:
        #         continue
        #
        #     # +2% 이상 상승 시 매수 (예시 로직)
        #     if change_pct > 2.0:
        #         signals.append(Signal(
        #             symbol=symbol,
        #             side=Side.BUY,
        #             quantity=1,
        #             order_type=OrderType.MARKET,
        #             reason=f"모멘텀 매수: {change_pct:.1f}% 상승",
        #         ))
        #
        # return signals
