"""데이터 모델 — 캔들, 주문, 포지션, 시그널 등"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ── Enums ──────────────────────────────────────────────

class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    """API에서 반환되는 주문 상태값들"""
    OPEN = "OPEN"          # 접수 대기 / 체결 대기
    PARTIAL = "PARTIAL"    # 부분 체결
    FILLED = "FILLED"      # 전량 체결
    CANCELED = "CANCELED"  # 취소
    REJECTED = "REJECTED"  # 거부
    EXPIRED = "EXPIRED"    # 만료


class TimeInForce(str, Enum):
    DAY = "DAY"
    CLS = "CLS"


# ── 시장 데이터 ────────────────────────────────────────

@dataclass
class Candle:
    """OHLCV 캔들 데이터"""
    symbol: str
    timestamp: str
    open: int
    high: int
    low: int
    close: int
    volume: int

    @classmethod
    def from_api(cls, symbol: str, data: dict) -> Candle:
        return cls(
            symbol=symbol,
            timestamp=data.get("timestamp", ""),
            open=_to_int(data.get("openPrice")),
            high=_to_int(data.get("highPrice")),
            low=_to_int(data.get("lowPrice")),
            close=_to_int(data.get("closePrice")),
            volume=_to_int(data.get("volume")),
        )


@dataclass
class Price:
    """현재가 데이터"""
    symbol: str
    last_price: int
    timestamp: str

    @classmethod
    def from_api(cls, data: dict) -> Price:
        return cls(
            symbol=data.get("symbol", ""),
            last_price=_to_int(data.get("lastPrice")),
            timestamp=data.get("timestamp", "")[:19].replace("T", " "),
        )


@dataclass
class OrderbookEntry:
    price: int
    volume: int


@dataclass
class Orderbook:
    symbol: str
    timestamp: str
    asks: list[OrderbookEntry] = field(default_factory=list)
    bids: list[OrderbookEntry] = field(default_factory=list)


@dataclass
class Trade:
    """체결 데이터"""
    symbol: str
    price: int
    volume: int
    timestamp: str


@dataclass
class StockInfo:
    """종목 기본 정보"""
    symbol: str
    name: str
    market: str
    currency: str
    listing_status: str = ""

    @classmethod
    def from_api(cls, data: dict) -> StockInfo:
        return cls(
            symbol=data.get("symbol", ""),
            name=data.get("name", ""),
            market=data.get("market", ""),
            currency=data.get("currency", ""),
            listing_status=data.get("listingStatus", ""),
        )


@dataclass
class Holding:
    """보유 주식"""
    symbol: str
    name: str
    quantity: int
    avg_price: int
    current_price: int
    evaluation_amount: int = 0
    profit_loss: int = 0
    profit_loss_rate: float = 0.0

    @classmethod
    def from_api(cls, data: dict) -> Holding:
        return cls(
            symbol=data.get("symbol", ""),
            name=data.get("stockName", data.get("name", "")),
            quantity=_to_int(data.get("quantity", data.get("holdQuantity", 0))),
            avg_price=_to_int(data.get("avgBuyPrice", data.get("avgPrice", 0))),
            current_price=_to_int(data.get("currentPrice", data.get("valuationPrice", 0))),
        )


# ── 주문 ──────────────────────────────────────────────

@dataclass
class Signal:
    """전략이 생성하는 매수/매도 시그널"""
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    price: int | None = None       # LIMIT 시 필수
    time_in_force: TimeInForce = TimeInForce.DAY
    reason: str = ""


@dataclass
class OrderResult:
    """주문 생성/정정/취소 결과"""
    order_id: str | None = None
    client_order_id: str | None = None
    status: str = ""
    symbol: str = ""
    side: str = ""
    quantity: int = 0
    price: int = 0
    message: str = ""

    @property
    def success(self) -> bool:
        return self.order_id is not None


@dataclass
class Fill:
    """체결 정보"""
    order_id: str
    symbol: str
    side: Side
    filled_quantity: int
    filled_price: int
    timestamp: str = ""


# ── 리스크/포지션 ────────────────────────────────────

@dataclass
class Portfolio:
    """포트폴리오 전체 현황"""
    total_assets: int = 0
    total_buying_power: int = 0
    holdings: list[Holding] = field(default_factory=list)


# ── 전략 컨텍스트 ───────────────────────────────────

@dataclass
class StrategyContext:
    """전략 평가에 필요한 전체 컨텍스트"""
    candles: dict[str, list[Candle]]       # symbol → 캔들 목록
    prices: dict[str, Price]                # symbol → 현재가
    orderbooks: dict[str, Orderbook]        # symbol → 호가
    holdings: list[Holding]                 # 보유 주식
    buying_power: int                       # 매수 가능 금액
    total_assets: int                       # 총 자산
    timestamp: datetime = field(default_factory=datetime.now)


# ── 유틸 함수 ────────────────────────────────────────

def _to_int(v: Any) -> int:
    """API 응답 값을 정수로 변환 (문자열/콤마 대응)"""
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _to_float(v: Any) -> float:
    """API 응답 값을 실수로 변환"""
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
