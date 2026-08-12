"""토스증권 Open API 클라이언트 — 전체 API 커버

인증, 시세/종목정보, 계좌/자산, 주문, 조건주문, 랭킹, 환율 등을 지원합니다.
Rate limit 자동 관리 및 429 재시도 포함.
"""

from __future__ import annotations

import requests
import time
from typing import Any

from .utils import DEFAULT_RATE_LIMITER
from .models import _to_int, _to_float


# ── API 엔드포인트 상수 ─────────────────────────────────

BASE_URL = "https://openapi.tossinvest.com"

# Rate limit 그룹 매핑
_ENDPOINT_GROUPS: dict[str, str] = {
    "/oauth2/token": "AUTH",
    "/api/v1/prices": "MARKET_DATA",
    "/api/v1/orderbook": "MARKET_DATA",
    "/api/v1/trades": "MARKET_DATA",
    "/api/v1/candles": "MARKET_DATA_CHART",
    "/api/v1/price-limits": "MARKET_DATA",
    "/api/v1/stocks": "STOCK",
    "/api/v1/stocks/": "STOCK",  # prefix match
    "/api/v1/accounts": "ACCOUNT",
    "/api/v1/holdings": "ASSET",
    "/api/v1/orders": "ORDER",
    "/api/v1/buying-power": "ORDER_INFO",
    "/api/v1/sellable-quantity": "ORDER_INFO",
    "/api/v1/commissions": "ORDER_INFO",
    "/api/v1/conditional-orders": "CONDITIONAL_ORDER",
    "/api/v1/exchange-rate": "MARKET_INFO",
    "/api/v1/market-calendar": "MARKET_INFO",
    "/api/v1/rankings": "RANKING",
    "/api/v1/market-indicators": "MARKET_INDICATOR",
}


def _get_rate_group(path: str) -> str:
    """경로에 해당하는 rate limit 그룹 반환"""
    for prefix, group in _ENDPOINT_GROUPS.items():
        if path.startswith(prefix):
            return group
    return "MARKET_DATA"  # 기본값


class TossClient:
    """토스증권 Open API 클라이언트

    Usage:
        client = TossClient(client_id="...", client_secret="...")
        client.init_account()  # 계좌 설정
        prices = client.prices(["005930", "000660"])
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        account_id: str | None = None,
        rate_limiter: Any | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_id: str | None = account_id
        self._token: str | None = None
        self._expires_at: float = 0.0
        self.session = requests.Session()
        self.rate_limiter = rate_limiter or DEFAULT_RATE_LIMITER

    # ── 인증 ──────────────────────────────────────────

    def _get_token(self) -> str:
        """OAuth2 토큰 발급/갱신 (Client Credentials Grant)"""
        if self._token and time.time() < self._expires_at:
            return self._token

        resp = self.session.post(
            f"{BASE_URL}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600) - 60
        return self._token

    def _headers(self, account: bool = False) -> dict[str, str]:
        """요청 헤더 생성"""
        h = {"Authorization": f"Bearer {self._get_token()}"}
        if account and self.account_id:
            h["X-Tossinvest-Account"] = self.account_id
        return h

    def _request(
        self,
        method: str,
        path: str,
        account: bool = False,
        params: dict | None = None,
        json_body: dict | None = None,
        rate_group: str | None = None,
    ) -> dict:
        """API 요청 (rate limit + 재시도 내장)"""
        group = rate_group or _get_rate_group(path)
        self.rate_limiter.wait(group)

        url = f"{BASE_URL}{path}"
        resp = self.session.request(
            method,
            url,
            headers=self._headers(account),
            params=params,
            json=json_body,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 계좌 초기화 ────────────────────────────────────

    def init_account(self) -> str | None:
        """첫 번째 계좌를 자동 선택. 반환: account_id"""
        if not self.account_id:
            accounts = self.accounts()
            if accounts:
                self.account_id = str(accounts[0].get("accountSeq", ""))
        return self.account_id

    # ── 시세 (Market Data) ──────────────────────────────

    def prices(self, symbols: list[str]) -> list[dict]:
        """현재가 조회 (다중 종목)

        Returns: [{symbol, lastPrice, timestamp}, ...]
        """
        data = self._request("GET", "/api/v1/prices", params={"symbols": ",".join(symbols)})
        return data.get("result", [])

    def orderbook(self, symbol: str) -> dict:
        """호가 조회

        Returns: {timestamp, asks: [{price, volume}], bids: [{price, volume}]}
        """
        return self._request("GET", "/api/v1/orderbook", params={"symbol": symbol}).get("result", {})

    def trades(self, symbol: str) -> list[dict]:
        """최근 체결 내역

        Returns: [{price, volume, timestamp}, ...]
        """
        data = self._request("GET", "/api/v1/trades", params={"symbol": symbol})
        result = data.get("result", [])
        return result if isinstance(result, list) else []

    def candles(self, symbol: str, interval: str = "1d", count: int = 30) -> list[dict]:
        """캔들 차트 조회

        Args:
            symbol: 종목 코드
            interval: "1m" (1분봉) 또는 "1d" (일봉)
            count: 조회 개수

        Returns: [{timestamp, openPrice, highPrice, lowPrice, closePrice, volume}, ...]
        """
        data = self._request(
            "GET", "/api/v1/candles",
            params={"symbol": symbol, "interval": interval, "count": count},
        )
        r = data.get("result", {})
        return r.get("candles", []) if isinstance(r, dict) else r

    def price_limits(self, symbol: str) -> dict:
        """상/하한가 조회"""
        return self._request("GET", "/api/v1/price-limits", params={"symbol": symbol}).get("result", {})

    # ── 종목 정보 (Stock Info) ────────────────────────

    def stocks(self, symbols: list[str]) -> list[dict]:
        """종목 기본 정보 조회 (다중 종목)

        Returns: [{symbol, name, market, currency, listingStatus}, ...]
        """
        return self._request("GET", "/api/v1/stocks", params={"symbols": ",".join(symbols)}).get("result", [])

    def list_stocks(self, market: str = "KR") -> list[dict]:
        """마켓별 전체 종목 조회"""
        return self._request("GET", "/api/v1/stocks/all", params={"market": market}).get("result", [])

    def warnings(self, symbol: str) -> dict:
        """매수 유의사항 (정리매매, 과열, 투자경고 등)"""
        return self._request("GET", f"/api/v1/stocks/{symbol}/warnings").get("result", {})

    def investor_trading(self, symbol: str) -> dict:
        """투자자별 매매동향 (개인·외국인·기관 일별 거래량)"""
        return self._request("GET", f"/api/v1/stocks/{symbol}/investor-trading").get("result", {})

    def program_trades(self, symbol: str) -> dict:
        """프로그램매매 동향 (차익·비차익 일별 거래량)"""
        return self._request("GET", f"/api/v1/stocks/{symbol}/program-trades").get("result", {})

    def short_selling(self, symbol: str) -> dict:
        """공매도 동향 (일별 거래량·거래대금·비중)"""
        return self._request("GET", f"/api/v1/stocks/{symbol}/short-selling").get("result", {})

    def credit_trades(self, symbol: str) -> dict:
        """신용거래 동향 (융자·대주 일별 수량·잔고·공여율)"""
        return self._request("GET", f"/api/v1/stocks/{symbol}/credit-trades").get("result", {})

    def securities_lending(self, symbol: str) -> dict:
        """대차거래 동향 (일별 체결·상환·잔고)"""
        return self._request("GET", f"/api/v1/stocks/{symbol}/securities-lending").get("result", {})

    # ── 계좌/자산 (Account · Asset) ───────────────────

    def accounts(self) -> list[dict]:
        """계좌 목록 조회

        Returns: [{accountSeq, accountName, accountType}, ...]
        """
        data = self._request("GET", "/api/v1/accounts", account=True)
        return data.get("result", [])

    def holdings(self) -> list[dict]:
        """보유 주식 조회 (종목별 상세 + 평가금액·손익 합산)"""
        data = self._request("GET", "/api/v1/holdings", account=True)
        return data.get("result", [])

    # ── 주문 (Order) ─────────────────────────────────

    def create_order(
        self,
        symbol: str,
        side: str,            # "BUY" or "SELL"
        order_type: str,      # "LIMIT" or "MARKET"
        quantity: int,
        price: int | None = None,         # LIMIT 시 필수
        time_in_force: str = "DAY",       # "DAY" or "CLS"
        client_order_id: str | None = None,  # 멱등성 키 (최대 36자)
        confirm_high_value: bool = False,  # 1억원 이상 주문 시 true
    ) -> dict:
        """주문 생성

        Args:
            symbol: 종목 코드
            side: 주문 방향 ("BUY" / "SELL")
            order_type: 호가 유형 ("LIMIT" / "MARKET")
            quantity: 주문 수량 (정수, 국내/지정가)
            price: 주문 가격 (LIMIT 시 필수, KR은 원 단위 정수)
            time_in_force: 유효 조건 ("DAY" / "CLS")
            client_order_id: 멱등성 키 (동일 값 재전송 시 동일 결과 반환, 10분 유효)
            confirm_high_value: 1억원 이상 주문 시 true

        Returns: {orderId, clientOrderId, symbol, side, orderType, status, ...}
        """
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "quantity": quantity,
            "timeInForce": time_in_force,
            "confirmHighValueOrder": confirm_high_value,
        }
        if price is not None and order_type == "LIMIT":
            body["price"] = price
        if client_order_id:
            body["clientOrderId"] = client_order_id

        return self._request(
            "POST", "/api/v1/orders",
            account=True, json_body=body,
        )

    def modify_order(
        self,
        order_id: str,
        price: int | None = None,
        quantity: int | None = None,
    ) -> dict:
        """주문 정정 (가격 또는 수량 변경)

        KR 주식: quantity 필수 (양의 정수)
        US 주식: quantity 불가, price만 변경
        """
        body: dict[str, Any] = {}
        if price is not None:
            body["price"] = price
        if quantity is not None:
            body["quantity"] = quantity

        return self._request(
            "POST", f"/api/v1/orders/{order_id}/modify",
            account=True, json_body=body,
        )

    def cancel_order(self, order_id: str) -> dict:
        """주문 취소 (이미 체결된 주문은 취소 불가)"""
        return self._request(
            "POST", f"/api/v1/orders/{order_id}/cancel",
            account=True, json_body={},
        )

    # ── 주문 조회 (Order History) ──────────────────────

    def list_orders(self, status: str | None = None) -> dict:
        """주문 목록 조회

        Args:
            status: "open" (대기중) 또는 "closed" (종료), 미지정 시 전체

        Returns: {totalCount, items: [...]}
        """
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/orders", account=True, params=params)

    def get_order(self, order_id: str) -> dict:
        """주문 상세 조회"""
        return self._request("GET", f"/api/v1/orders/{order_id}", account=True)

    # ── 거래 가능 정보 (Order Info) ───────────────────

    def buying_power(self) -> dict:
        """매수 가능 금액 조회 (현금 기반, KRW/USD)"""
        return self._request("GET", "/api/v1/buying-power", account=True).get("result", {})

    def sellable_quantity(self, symbol: str) -> dict:
        """판매 가능 수량 조회"""
        return self._request(
            "GET", "/api/v1/sellable-quantity",
            account=True, params={"symbol": symbol},
        ).get("result", {})

    def commissions(self) -> dict:
        """매매 수수료 조회 (KR/US 시장별)"""
        return self._request("GET", "/api/v1/commissions", account=True).get("result", {})

    # ── 조건주문 (Conditional Order) ──────────────────

    def create_conditional_order(
        self,
        symbol: str,
        order_type: str = "SINGLE",   # "SINGLE" / "OCO" / "OTO"
        conditions: list[dict] | None = None,
        **kwargs,
    ) -> dict:
        """조건주문 등록

        조건부 자동 매매: 지정 가격 도달 시 주문 자동 생성.

        Args:
            symbol: 종목 코드
            order_type: "SINGLE" (단일) / "OCO" (둘 중 하나) / "OTO" (순차)
            conditions: 조건 목록 [{price, side, orderType, quantity, price: limitPrice}]
        """
        body: dict[str, Any] = {
            "symbol": symbol,
            "type": order_type,
        }
        if conditions:
            body["conditions"] = conditions
        body.update(kwargs)

        return self._request(
            "POST", "/api/v1/conditional-orders",
            account=True, json_body=body,
        )

    def list_conditional_orders(self, status: str = "OPEN") -> dict:
        """조건주문 목록 조회

        Args:
            status: "OPEN" (진행 중) 또는 "CLOSED" (종료)
        """
        return self._request(
            "GET", "/api/v1/conditional-orders",
            account=True, params={"status": status},
        )

    def get_conditional_order(self, conditional_order_id: str) -> dict:
        """조건주문 상세 조회"""
        return self._request(
            "GET", f"/api/v1/conditional-orders/{conditional_order_id}",
            account=True,
        )

    def modify_conditional_order(
        self,
        conditional_order_id: str,
        **kwargs,
    ) -> dict:
        """조건주문 수정"""
        return self._request(
            "POST", f"/api/v1/conditional-orders/{conditional_order_id}/modify",
            account=True, json_body=kwargs,
        )

    def cancel_conditional_order(self, conditional_order_id: str) -> dict:
        """조건주문 취소"""
        return self._request(
            "DELETE", f"/api/v1/conditional-orders/{conditional_order_id}",
            account=True,
        )

    # ── 기타 (Market Info · Ranking) ──────────────────

    def exchange_rate(self) -> dict:
        """KRW↔USD 환율 조회"""
        return self._request("GET", "/api/v1/exchange-rate").get("result", {})

    def market_calendar(self, country: str = "KR") -> dict:
        """장 운영 정보

        Args:
            country: "KR" (국내) 또는 "US" (미국)
        """
        return self._request("GET", f"/api/v1/market-calendar/{country}").get("result", {})

    def rankings(
        self,
        sort_by: str = "tradeAmount",
        duration: str = "daily",
        market: str = "KR",
        page: int = 1,
        size: int = 30,
    ) -> dict:
        """주식 랭킹 조회

        Args:
            sort_by: "tradeAmount" (거래대금), "volume" (거래량), "changeRate" (등락률)
            duration: "realtime" / "daily" / "weekly" / "monthly"
                     TOP_GAINERS/TOP_LOSERS는 realtime 불가
            market: "KR" (국내)
            page: 페이지 번호
            size: 페이지 크기
        """
        return self._request(
            "GET", "/api/v1/rankings",
            params={
                "sortBy": sort_by,
                "duration": duration,
                "market": market,
                "page": page,
                "size": size,
            },
        ).get("result", {})

    # ── 시장 지표 ────────────────────────────────────

    def market_indicator_prices(self) -> dict:
        """시장 지표 현재가 (코스피·코스닥·국채)"""
        return self._request("GET", "/api/v1/market-indicators/prices").get("result", {})

    def market_indicator_candles(self, symbol: str, interval: str = "1d", count: int = 30) -> list[dict]:
        """시장 지표 캔들 차트 (코스피, 코스닥 등)"""
        data = self._request(
            "GET", f"/api/v1/market-indicators/{symbol}/candles",
            params={"interval": interval, "count": count},
        )
        r = data.get("result", {})
        return r.get("candles", []) if isinstance(r, dict) else r

    def market_indicator_investor_trading(self, symbol: str) -> dict:
        """시장 지표 투자자별 매매대금 (코스피·코스닥만)"""
        return self._request(
            "GET", f"/api/v1/market-indicators/{symbol}/investor-trading",
        ).get("result", {})
