"""유틸리티 함수 — 시간, 포맷팅, rate limit 등"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


# ── KST 시간대 ──────────────────────────────────────────

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 KST 시간"""
    return datetime.now(KST)


def format_kst(dt: datetime | None = None) -> str:
    """KST 시간을 문자열로 포맷팅"""
    if dt is None:
        dt = now_kst()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def is_market_open(dt: datetime | None = None) -> bool:
    """한국 장 시간 체크 (09:00 ~ 15:30 KST, 평일만)"""
    dt = dt or now_kst()
    if dt.weekday() >= 5:
        return False
    t = dt.hour * 100 + dt.minute
    return 900 <= t <= 1530


# ── Rate Limit ──────────────────────────────────────────

@dataclass
class RateLimiter:
    """API 그룹별 TPS 제한 관리"""
    limits: dict[str, int] = field(default_factory=lambda: {
        "ORDER": 10,
        "ORDER_HISTORY": 5,
        "ORDER_INFO": 6,
        "MARKET_DATA": 15,
        "MARKET_DATA_CHART": 20,
        "STOCK": 5,
        "ACCOUNT": 1,
        "ASSET": 5,
        "CONDITIONAL_ORDER": 5,
        "CONDITIONAL_ORDER_HISTORY": 10,
        "MARKET_INFO": 3,
        "RANKING": 5,
    })
    _last_call: dict[str, float] = field(default_factory=dict)
    _min_interval: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        self._min_interval = {k: 1.0 / v for k, v in self.limits.items()}

    def wait(self, group: str) -> None:
        """해당 그룹의 rate limit을 준수하도록 대기"""
        min_iv = self._min_interval.get(group, 0.2)
        last = self._last_call.get(group, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < min_iv:
            time.sleep(min_iv - elapsed)
        self._last_call[group] = time.monotonic()


# ── Retry ──────────────────────────────────────────────

def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
):
    """지수 백오프 + jitter로 재시도 (429 등에 사용)"""
    import requests

    for attempt in range(max_retries):
        try:
            return func()
        except requests.exceptions.HTTPError as e:
            resp = e.response
            if resp is not None and resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "0") or "0")
                if retry_after > 0:
                    time.sleep(retry_after)
                else:
                    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                    time.sleep(delay)
                continue
            raise
        except requests.exceptions.ConnectionError:
            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                time.sleep(delay)
                continue
            raise

    return func()  # 마지막 시도


# ── 포맷팅 ────────────────────────────────────────────

def fmt_num(n: int | float) -> str:
    """숫자를 천 단위 콤마로 포맷팅"""
    if isinstance(n, float):
        return f"{n:,.0f}"
    return f"{n:,}"


def fmt_pct(n: float) -> str:
    """퍼센트 포맷팅"""
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"


# ── 전역 Rate Limiter ─────────────────────────────────

DEFAULT_RATE_LIMITER = RateLimiter()
