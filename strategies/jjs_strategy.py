"""김정수 스타일 세력주 데이트레이딩 전략

핵심:
1. 거래량 급증(1년 최대 수준) + 당일 급등 종목 스캔
2. 15:00 이후 종가 근처 진입 판단
3. 렌코 차트(1분봉 기반)로 트렌드 확인
4. 1계좌 4포지션 분할 물타기 (-10%마다 추가 매수)
5. 각 포지션별 +10% 익절, 당일 15:20 강제 청산
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models import Signal, StrategyContext, Holding, Side, OrderType, Candle
from src.strategy.base import BaseStrategy
from src.utils import fmt_num, _to_int


# ── 렌코 차트 계산 ───────────────────────────────────

@dataclass
class RenkoBrick:
    """렌코 브릭"""
    price: float
    direction: int  # 1 (상승) / -1 (하락)
    timestamp: str


def build_renko(candles_1m: list[dict], brick_size_pct: float = 1.5) -> list[RenkoBrick]:
    """1분봉으로 렌코 차트 생성

    Args:
        candles_1m: 1분봉 데이터 (close price 기준)
        brick_size_pct: 브릭 크기 (%). 1.5%면 10,000원 주식은 150원 브릭

    Returns:
        렌코 브릭 리스트 (시간순)
    """
    if not candles_1m:
        return []

    # 첫 브릭
    first_close = _to_int(candles_1m[0].get("closePrice", 0))
    if first_close <= 0:
        return []

    brick_size = round(first_close * brick_size_pct / 100)
    if brick_size <= 0:
        return []

    bricks: list[RenkoBrick] = []
    current_price = first_close

    for c in candles_1m:
        close = _to_int(c.get("closePrice", 0))
        if close <= 0:
            continue

        # 상승 브릭 생성
        if close > current_price:
            new_bricks = int((close - current_price) / brick_size)
            for _ in range(new_bricks):
                current_price += brick_size
                bricks.append(RenkoBrick(
                    price=current_price,
                    direction=1,
                    timestamp=c.get("timestamp", ""),
                ))

        # 하락 브릭 생성 (마지막 브릭 방향이 상승이거나 첫 브릭일 때만)
        elif close < current_price:
            # 하락 반전: 마지막 상승 브릭의 시작가(현재가 - brick_size) 아래로 내려가면 반전
            reversal_level = current_price - brick_size
            if close <= reversal_level:
                new_bricks = int((reversal_level - close) / brick_size) + 1
                for i in range(new_bricks):
                    current_price = reversal_level - (i * brick_size)
                    bricks.append(RenkoBrick(
                        price=current_price,
                        direction=-1,
                        timestamp=c.get("timestamp", ""),
                    ))

    return bricks


def renko_trend(bricks: list[RenkoBrick], lookback: int = 5) -> int:
    """렌코 트렌드 판단

    Returns:
        1: 상승 트렌드 (최근 lookback개 브릭이 대부분 상승)
        -1: 하락 트렌드
        0: 불확실
    """
    if len(bricks) < lookback:
        return 0

    recent = bricks[-lookback:]
    up_count = sum(1 for b in recent if b.direction == 1)
    down_count = sum(1 for b in recent if b.direction == -1)

    if up_count >= math.ceil(lookback * 0.7):
        return 1
    elif down_count >= math.ceil(lookback * 0.7):
        return -1
    return 0


# ── 포지션 관리 (1계좌 4포지션) ──────────────────────

POSITION_FILE = Path("data/positions.json")


@dataclass
class Position:
    """단일 포지션 (각 진입별 독립 관리)"""
    symbol: str
    slot: int              # 1~4 (몇 번째 진입인지)
    entry_price: int       # 진입가
    quantity: int          # 수량
    entry_time: str        # 진입 시간
    target_price: int      # 익절가 (+10%)
    stop_price: int | None = None  # 손절가 (선택)
    status: str = "OPEN"   # OPEN / CLOSED

    @property
    def target_pct(self) -> float:
        return 10.0  # +10% 익절

    def check_exit(self, current_price: int) -> str | None:
        """청산 조건 체크

        Returns:
            "TAKE_PROFIT" | "STOP_LOSS" | None
        """
        if self.status != "OPEN":
            return None
        if current_price >= self.target_price:
            return "TAKE_PROFIT"
        if self.stop_price and current_price <= self.stop_price:
            return "STOP_LOSS"
        return None


@dataclass
class SymbolPositions:
    """종목별 4포지션 관리"""
    symbol: str
    positions: list[Position] = field(default_factory=list)
    first_entry_price: int = 0  # 1차 진입가 (물타기 기준)

    def get_open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "OPEN"]

    def get_next_slot(self) -> int:
        """다음 빈 슬롯 번호"""
        used = {p.slot for p in self.positions}
        for i in range(1, 5):
            if i not in used:
                return i
        return 0  # 모든 슬롯 사용 중

    def calc_next_buy_price(self) -> int | None:
        """다음 물타기 진입가 (이전 진입가 -10%)"""
        if not self.first_entry_price:
            return None
        slot = self.get_next_slot()
        if slot <= 1:
            return self.first_entry_price
        # slot 2: -10%, slot 3: -20%, slot 4: -30%
        discount = (slot - 1) * 0.10
        return int(self.first_entry_price * (1 - discount))

    @property
    def has_open_position(self) -> bool:
        return len(self.get_open_positions()) > 0

    def is_all_closed(self) -> bool:
        return all(p.status == "CLOSED" for p in self.positions)


class PositionManager:
    """포지션 전체 관리 (JSON 파일 저장)"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "positions.json"
        self._positions: dict[str, SymbolPositions] = {}

    def load(self) -> None:
        """JSON 파일에서 포지션 로드"""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for sym, data in raw.items():
                    sp = SymbolPositions(symbol=sym)
                    sp.first_entry_price = data.get("first_entry_price", 0)
                    for pd in data.get("positions", []):
                        sp.positions.append(Position(**pd))
                    self._positions[sym] = sp
            except Exception:
                self._positions = {}

    def save(self) -> None:
        """JSON 파일에 포지션 저장"""
        data = {}
        for sym, sp in self._positions.items():
            data[sym] = {
                "first_entry_price": sp.first_entry_price,
                "positions": [asdict(p) for p in sp.positions],
            }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, symbol: str) -> SymbolPositions:
        if symbol not in self._positions:
            self._positions[symbol] = SymbolPositions(symbol=symbol)
        return self._positions[symbol]

    def add_position(self, symbol: str, entry_price: int, quantity: int) -> Position | None:
        """새 포지션 추가"""
        sp = self.get(symbol)
        slot = sp.get_next_slot()
        if slot == 0:
            return None  # 슬롯 꽉참

        if not sp.first_entry_price:
            sp.first_entry_price = entry_price

        pos = Position(
            symbol=symbol,
            slot=slot,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            target_price=int(entry_price * 1.10),  # +10%
            status="OPEN",
        )
        sp.positions.append(pos)
        self.save()
        return pos

    def close_position(self, symbol: str, slot: int) -> None:
        """포지션 청산"""
        sp = self.get(symbol)
        for p in sp.positions:
            if p.slot == slot and p.status == "OPEN":
                p.status = "CLOSED"
                break
        self.save()

    def all_symbols(self) -> list[str]:
        return list(self._positions.keys())


# ── 메인 전략 ──────────────────────────────────────────

@dataclass
class ScanResult:
    """스캔 결과 (매수 후보 종목)"""
    symbol: str
    name: str
    current_price: int
    change_pct: float         # 당일 등락률
    volume_ratio: float        # 거래량 비율 (최근 평균 대비)
    volume_rank: float         # 1년 내 거래량 순위 (0~1, 1이 최대)
    renko_trend: int           # 렌코 트렌드 (1/0/-1)


class JjsStrategy(BaseStrategy):
    """김정수 스타일 세력주 전략"""

    def __init__(
        self,
        client: Any = None,
        position_manager: PositionManager | None = None,
        volume_avg_days: int = 20,
        volume_spike_threshold: float = 3.0,   # 평균 대비 3배 이상
        min_change_pct: float = 3.0,            # 최소 +3% 이상 상승
        renko_brick_pct: float = 1.5,           # 렌코 브릭 크기 (%)
        entry_after_hour: int = 15,             # 15시 이후 진입
        force_exit_before_min: int = 20,         # 15:20 전 강제 청산
        slot_budget_pct: float = 25.0,          # 각 슬롯에 할당할 자산 비율 (%)
    ):
        self.client = client
        self.pm = position_manager or PositionManager()
        self.volume_avg_days = volume_avg_days
        self.volume_spike_threshold = volume_spike_threshold
        self.min_change_pct = min_change_pct
        self.renko_brick_pct = renko_brick_pct
        self.entry_after_hour = entry_after_hour
        self.force_exit_before_min = force_exit_before_min
        self.slot_budget_pct = slot_budget_pct

        self.pm.load()

    def name(self) -> str:
        return "김정수 세력주"

    def _scan_candidates(self, context: StrategyContext) -> list[ScanResult]:
        """거래대금 랭킹에서 후보 종목 스캔"""
        candidates = []

        if not self.client:
            return candidates

        try:
            # 거래대금 상위 종목 가져오기
            ranking = self.client.rankings(
                sort_by="tradeAmount",
                duration="daily",
                market="KR",
                size=50,
            )
            items = ranking.get("items", [])
        except Exception:
            items = []

        for item in items:
            symbol = item.get("symbol", "")
            name = item.get("stockName", item.get("name", ""))
            change_pct = float(item.get("changeRate", 0))
            current_price = _to_int(item.get("price", item.get("closePrice", 0)))

            if change_pct < self.min_change_pct:
                continue
            if current_price <= 0:
                continue

            # 1년치 일봉으로 거래량 순위 체크
            volume_ratio = 0.0
            volume_rank = 0.0
            try:
                candles_1y = self.client.candles(symbol, "1d", 250)
                if candles_1y:
                    volumes = [_to_int(c.get("volume", 0)) for c in candles_1y]
                    today_vol = volumes[-1] if volumes else 0
                    avg_vol = sum(volumes[-(self.volume_avg_days + 1):-1]) / self.volume_avg_days if len(volumes) > self.volume_avg_days else sum(volumes[:-1]) / max(len(volumes) - 1, 1)

                    volume_ratio = today_vol / avg_vol if avg_vol > 0 else 0
                    # 1년 내 거래량 순위 (0~1)
                    sorted_vols = sorted(volumes, reverse=True)
                    if today_vol in sorted_vols:
                        rank_idx = sorted_vols.index(today_vol)
                        volume_rank = 1.0 - (rank_idx / len(sorted_vols))
            except Exception:
                pass

            if volume_ratio < self.volume_spike_threshold and volume_rank < 0.8:
                continue

            # 렌코 트렌드 확인 (1분봉)
            renko_trend = 0
            try:
                candles_1m = self.client.candles(symbol, "1m", 100)
                bricks = build_renko(candles_1m, self.renko_brick_pct)
                renko_trend = renko_trend(bricks, lookback=5)
            except Exception:
                pass

            candidates.append(ScanResult(
                symbol=symbol,
                name=name,
                current_price=current_price,
                change_pct=change_pct,
                volume_ratio=volume_ratio,
                volume_rank=volume_rank,
                renko_trend=renko_trend,
            ))

        return candidates

    def _check_existing_positions(self, context: StrategyContext) -> list[Signal]:
        """기존 포지션 체크 — 익절, 손절, 강제 청산, 물타기"""
        signals = []

        for symbol in list(self.pm.all_symbols()):
            sp = self.pm.get(symbol)
            price_info = context.prices.get(symbol)
            if not price_info:
                continue

            current_price = price_info.last_price

            # 각 포지션 체크
            for pos in sp.get_open_positions():
                exit_reason = pos.check_exit(current_price)

                if exit_reason == "TAKE_PROFIT":
                    # +10% 익절
                    signals.append(Signal(
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=pos.quantity,
                        order_type=OrderType.MARKET,
                        reason=f"익절: {pos.slot}차 포지션 (+10%, 진입가 {fmt_num(pos.entry_price)} → {fmt_num(current_price)})",
                    ))
                    self.pm.close_position(symbol, pos.slot)

                elif exit_reason == "STOP_LOSS":
                    # 손절
                    signals.append(Signal(
                        symbol=symbol,
                        side=Side.SELL,
                        quantity=pos.quantity,
                        order_type=OrderType.MARKET,
                        reason=f"손절: {pos.slot}차 포지션 (진입가 {fmt_num(pos.entry_price)} → {fmt_num(current_price)})",
                    ))
                    self.pm.close_position(symbol, pos.slot)

            # 물타기 체크 — 현재가가 다음 물타기 가격 이하로 떨어졌으면
            if sp.has_open_position:
                next_price = sp.calc_next_buy_price()
                if next_price and current_price <= next_price:
                    slot = sp.get_next_slot()
                    if slot > 0:
                        signals.append(Signal(
                            symbol=symbol,
                            side=Side.BUY,
                            quantity=0,  # 엔진에서 계산
                            order_type=OrderType.LIMIT,
                            price=current_price,
                            reason=f"물타기: {slot}차 진입 (목표가 {fmt_num(next_price)}, 현재가 {fmt_num(current_price)})",
                        ))

        return signals

    def _check_force_exit(self, context: StrategyContext) -> list[Signal]:
        """15:20 강제 청산"""
        from src.utils import now_kst
        now = now_kst()

        if now.hour != self.entry_after_hour or now.minute < self.force_exit_before_min:
            return []

        signals = []
        for symbol in self.pm.all_symbols():
            sp = self.pm.get(symbol)
            for pos in sp.get_open_positions():
                signals.append(Signal(
                    symbol=symbol,
                    side=Side.SELL,
                    quantity=pos.quantity,
                    order_type=OrderType.MARKET,
                    reason=f"강제 청산: {pos.slot}차 포지션 (15:20 마감)",
                ))
                self.pm.close_position(symbol, pos.slot)

        return signals

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """전체 평가: 강제 청산 → 기존 포지션 관리 → 새 진입"""

        signals: list[Signal] = []

        # 1. 강제 청산 체크 (15:20)
        signals.extend(self._check_force_exit(context))

        # 2. 기존 포지션 관리 (익절/손절/물타기)
        signals.extend(self._check_existing_positions(context))

        # 3. 새로운 진입 후보 스캔
        from src.utils import now_kst
        now = now_kst()

        if now.hour < self.entry_after_hour:
            # 15시 이전: 후보만 스캔해서 보고
            candidates = self._scan_candidates(context)
            if candidates:
                for c in candidates[:10]:
                    # 로그만 (아직 매수 안 함)
                    pass
            return signals

        # 15시 이후: 후보 중 렌코 상승 트렌드 종목에 매수
        candidates = self._scan_candidates(context)
        for c in candidates:
            if c.renko_trend != 1:
                continue  # 렌코 상승 트렌드가 아니면 패스

            sp = self.pm.get(c.symbol)
            if sp.has_open_position:
                continue  # 이미 포지션 있으면 패스

            # 각 슬롯 예산 계산
            slot_qty = self._calc_slot_quantity(context.total_assets, c.current_price)

            signals.append(Signal(
                symbol=c.symbol,
                side=Side.BUY,
                quantity=slot_qty,
                order_type=OrderType.LIMIT,
                price=c.current_price,
                reason=f"세력 진입: 거래량 {c.volume_ratio:.1f}배, 등락 {c.change_pct:+.1f}%, 렌코 상승, {c.name}",
            ))

            # 첫 포지션 기록
            self.pm.add_position(c.symbol, c.current_price, slot_qty)

        return signals

    def _calc_slot_quantity(self, total_assets: int, price: int) -> int:
        """각 슬롯당 매수 수량 (총 자산의 slot_budget_pct% / 현재가)"""
        if price <= 0 or total_assets <= 0:
            return 0
        budget = int(total_assets * self.slot_budget_pct / 100)
        return max(budget // price, 1)  # 최소 1주
