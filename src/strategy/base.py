"""전략 기반 클래스 — 모든 매매 전략은 이 클래스를 상속해야 합니다."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Signal, StrategyContext, Fill


class BaseStrategy(ABC):
    """매매 전략 추상 클래스

    구현 방법:
        1. 이 클래스를 상속
        2. `name()` — 전략 이름 반환
        3. `evaluate()` — 시장 데이터 분석 → 매수/매도 시그널 반환
        4. (선택) `on_fill()` — 주문 체결 시 콜백
    """

    @abstractmethod
    def name(self) -> str:
        """전략 고유 이름"""
        ...

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """시장 데이터와 보유 상태를 분석해 매수/매도 시그널 반환

        Args:
            context: 종목 캔들, 현재가, 호가, 보유, 매수가능금액 등

        Returns:
            매수/매도 시그널 목록 (빈 리스트 = 아무것도 안 함)
        """
        ...

    def on_fill(self, fill: Fill) -> None:
        """주문 체결 시 콜백 (선택적 오버라이드)

        기본 구현: 아무 동작 없음.
        전략이 체결 후 포지션 관리가 필요한 경우 오버라이드.

        Args:
            fill: 체결 정보 (종목, 방향, 수량, 가격)
        """
        pass
