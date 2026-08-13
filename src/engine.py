"""메인 트레이딩 엔진 — 루프, 시그널 수집 → 전략 실행 → 리스크 필터 → 주문"""

from __future__ import annotations

import importlib
import signal as sig_module
import sys
import time
from typing import TYPE_CHECKING

from .client import TossClient
from .config import AppConfig
from .db import TradingDB, DbConfig
from .models import (
    Candle, Holding, Orderbook, OrderbookEntry, Price, Signal,
    StrategyContext, _to_int,
)
from .notify import NotifyLogger
from .risk import RiskManager
from .strategy.base import BaseStrategy
from .utils import fmt_num, format_kst, is_market_open, now_kst

if TYPE_CHECKING:
    pass


class TradingEngine:
    """트레이딩 엔진

    각 루프:
        1. 시장 데이터 수집 (캔들, 현재가, 호가)
        2. 계좌 정보 수집 (보유, 매수가능금액)
        3. 전략 평가 → 시그널 생성
        4. 리스크 필터 → 위험 시그널 차단
        5. 주문 실행 (dry-run / 실전)
        6. 로그/알림
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.client = TossClient(
            client_id=config.api.client_id,
            client_secret=config.api.client_secret,
            account_id=config.api.account_id,
        )
        self.db = TradingDB(config.db)
        self.logger = NotifyLogger(
            log_to_stdout=config.notify.log_to_stdout,
            webhook_url=config.notify.webhook_url,
            dry_run=config.engine.dry_run,
        )
        self.risk = RiskManager(config.risk, self.logger)
        self.strategies: list[BaseStrategy] = []
        self._running = False
        self._flask_app = None

    def load_strategies(self) -> None:
        """설정에 정의된 전략 클래스들을 동적 로드"""
        self.strategies = []
        for path in self.config.strategies:
            try:
                module_path, class_name = path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                strategy_cls = getattr(module, class_name)
                if not issubclass(strategy_cls, BaseStrategy):
                    raise TypeError(f"{class_name} is not a BaseStrategy subclass")
                # client가 필요한 전략이면 주입
                try:
                    strategy = strategy_cls(client=self.client, db=self.db)
                except TypeError:
                    try:
                        strategy = strategy_cls(client=self.client)
                    except TypeError:
                        strategy = strategy_cls()
                self.strategies.append(strategy)
                self.logger.info(f"전략 로드: {strategy.name()}")
            except Exception as e:
                self.logger.error(f"전략 로드 실패 ({path}): {e}")

    def initialize(self) -> None:
        """엔진 초기화 — DB, 계좌 설정, 전략 로드"""
        self.logger.info("=== 토스증권 트레이딩 봇 초기화 ===")
        self.logger.info(f"Dry-run: {'ON' if self.config.engine.dry_run else 'OFF (⚠️ 실전 모드!)'}")

        # DB 초기화
        try:
            self.db.init_schema()
            self.logger.info("DB 연동 완료")
            self.db.update_bot_status(status="INITIALIZING")
            self.db.reset_daily_pnl()
        except Exception as e:
            self.logger.error(f"DB 초기화 실패: {e}")
            self.logger.info("DB 없이 JSON 모드로 동작합니다")

        # 계좌 설정
        account_id = self.client.init_account()
        if account_id:
            self.logger.info(f"계좌: {account_id}")
        else:
            self.logger.error("계좌를 찾을 수 없습니다. config.yaml에서 account_id를 설정하세요.")
            sys.exit(1)

        # 전략 로드
        self.load_strategies()
        if not self.strategies:
            self.logger.warn("활성 전략이 없습니다. config.yaml의 strategies를 확인하세요.")

        # 매수가능금액으로 일일 손실 추적 초기화
        try:
            bp = self.client.buying_power()
            total = _to_int(bp.get("totalBuyingPower", 0))
            if total > 0:
                self.risk.reset_daily(total)
        except Exception:
            pass

        self.logger.info("초기화 완료\n")

    def _fetch_market_data(self) -> tuple[
        dict[str, list[Candle]],
        dict[str, Price],
        dict[str, Orderbook],
    ]:
        """감시 종목의 시장 데이터 수집"""
        symbols = self.config.engine.watch_symbols
        candles_map: dict[str, list[Candle]] = {}
        prices_map: dict[str, Price] = {}
        orderbooks_map: dict[str, Orderbook] = {}

        # 현재가 (다중 종목 한 번에)
        try:
            prices_data = self.client.prices(symbols)
            for p in prices_data:
                symbol = p.get("symbol", "")
                prices_map[symbol] = Price.from_api(p)
        except Exception as e:
            self.logger.warn(f"현재가 수집 실패: {e}")

        # 개별 종목 데이터
        for symbol in symbols:
            # 캔들
            try:
                candle_data = self.client.candles(symbol, "1d", 30)
                candles_map[symbol] = [Candle.from_api(symbol, c) for c in candle_data]
            except Exception as e:
                self.logger.warn(f"{symbol} 캔들 수집 실패: {e}")
                candles_map[symbol] = []

            time.sleep(0.2)  # API 호출 간 간격

            # 호가
            try:
                ob_data = self.client.orderbook(symbol)
                asks = [OrderbookEntry(price=_to_int(a["price"]), volume=_to_int(a["volume"])) for a in ob_data.get("asks", [])]
                bids = [OrderbookEntry(price=_to_int(b["price"]), volume=_to_int(b["volume"])) for b in ob_data.get("bids", [])]
                orderbooks_map[symbol] = Orderbook(
                    symbol=symbol,
                    timestamp=ob_data.get("timestamp", ""),
                    asks=asks,
                    bids=bids,
                )
            except Exception as e:
                self.logger.warn(f"{symbol} 호가 수집 실패: {e}")
                orderbooks_map[symbol] = Orderbook(symbol=symbol, timestamp="")

        return candles_map, prices_map, orderbooks_map

    def _fetch_account_data(self) -> tuple[list[Holding], int, int]:
        """계좌 데이터 수집 (보유, 매수가능금액)"""
        holdings: list[Holding] = []
        buying_power = 0
        total_assets = 0

        try:
            raw_holdings = self.client.holdings()
            for h in raw_holdings:
                if not isinstance(h, dict):
                    self.logger.warn(f"보유 항목 타입 이상: {type(h).__name__} = {str(h)[:200]}")
                    continue
                holdings.append(Holding.from_api(h))
        except Exception as e:
            self.logger.warn(f"보유 수집 실패: {e}")

        try:
            bp = self.client.buying_power()
            buying_power = _to_int(bp.get("totalBuyingPower", 0))
        except Exception:
            pass

        try:
            for h in holdings:
                total_assets += h.quantity * h.current_price
            total_assets += buying_power
        except Exception:
            pass

        return holdings, buying_power, total_assets

    def _execute_signal(self, signal: Signal) -> None:
        """시그널을 실제 주문으로 실행"""
        if self.config.engine.dry_run:
            self.logger.info(
                f"📝[DRY] 주문 (실행 안 함): {signal.side.value} {signal.symbol} "
                f"x{signal.quantity} @ {fmt_num(signal.price) if signal.price else 'MARKET'} "
                f"— {signal.reason}"
            )
            return

        try:
            result = self.client.create_order(
                symbol=signal.symbol,
                side=signal.side.value,
                order_type=signal.order_type.value,
                quantity=signal.quantity,
                price=signal.price,
                time_in_force=signal.time_in_force.value,
                confirm_high_value=True,  # 안전하게 항상 켜기
            )
            order_id = result.get("result", {}).get("orderId") or result.get("orderId")
            if order_id:
                self.logger.info(
                    f"✅ 주문 성공: {signal.side.value} {signal.symbol} "
                    f"x{signal.quantity} @ {fmt_num(signal.price) if signal.price else 'MARKET'} "
                    f"— orderId: {order_id}"
                )
            else:
                self.logger.error(f"❌ 주문 실패: {result}")
        except Exception as e:
            self.logger.error(f"❌ 주문 오류: {e}")

    def run_once(self) -> None:
        """한 번 실행 (시그널 수집 → 리스크 필터 → 주문 → 종료)"""
        self.logger.info(f"--- 한 번 실행 ({format_kst()}) ---")

        # 시장 데이터 수집
        candles, prices, orderbooks = self._fetch_market_data()

        # 계좌 데이터 수집
        holdings, buying_power, total_assets = self._fetch_account_data()

        # 컨텍스트 구성
        context = StrategyContext(
            candles=candles,
            prices=prices,
            orderbooks=orderbooks,
            holdings=holdings,
            buying_power=buying_power,
            total_assets=total_assets,
        )

        # 시그널 수집
        all_signals: list[Signal] = []
        for strategy in self.strategies:
            try:
                signals = strategy.evaluate(context)
                for s in signals:
                    s.reason = f"[{strategy.name()}] {s.reason}"
                    self.logger.signal(s)
                all_signals.extend(signals)
            except Exception as e:
                self.logger.error(f"전략 평가 오류 ({strategy.name()}): {e}")

        # 리스크 필터 + 주문
        for signal in all_signals:
            risk_result = self.risk.check(signal, holdings, total_assets, buying_power)
            if not risk_result.passed:
                self.logger.risk_block(risk_result.reason)
                continue
            self._execute_signal(signal)

        self.logger.info(f"--- 실행 완료 (시그널 {len(all_signals)}건) ---")

    def run_loop(self) -> None:
        """주기적 루프 실행 (장시간 동안)"""
        self._running = True
        self.logger.info(f"=== 루프 시작 (간격: {self.config.engine.interval_seconds}초) ===")

        # SIGINT/SIGTERM으로 graceful shutdown
        def _shutdown(signum, frame):
            self.logger.info("Graceful shutdown 요청...")
            self._running = False

        sig_module.signal(sig_module.SIGINT, _shutdown)
        sig_module.signal(sig_module.SIGTERM, _shutdown)

        while self._running:
            if is_market_open():
                self.run_once()
            else:
                self.logger.info(f"장외 시간 ({format_kst()})")

            # 대기 (interruptible)
            for _ in range(self.config.engine.interval_seconds):
                if not self._running:
                    break
                time.sleep(1)

        self.logger.info("=== 루프 종료 ===")

    def start_api_server(self) -> None:
        """Flask REST API 서버 (백그라운드)"""
        from .api import create_app
        self._flask_app = create_app(self.db, self)

        import threading
        self._flask_thread = threading.Thread(
            target=self._flask_app.run,
            kwargs={
                "host": self.config.flask.host,
                "port": self.config.flask.port,
                "use_reloader": False,
            },
            daemon=True,
        )
        self._flask_thread.start()
        self.logger.info(f"API 서버: http://{self.config.flask.host}:{self.config.flask.port}")

    def run(self) -> None:
        """API 서버 + 루프 동시 실행"""
        self.initialize()
        self.start_api_server()
        self.db.update_bot_status(status="RUNNING")
        self.run_loop()
        self.db.update_bot_status(status="STOPPED")

