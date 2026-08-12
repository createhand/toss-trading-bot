# 토스증권 트레이딩 봇

토스증권 Open API 기반 트레이딩 봇. 전략 패턴(Strategy Pattern)으로 유연하게 매매 전략을 추가/교체할 수 있습니다.

## 특징

- **전략 패턴**: `BaseStrategy` 상속으로 자신만의 매매 전략 구현
- **토스증권 Open API 전체 커버**: 시세, 호가, 체결, 캔들, 주문, 조건주문, 보유, 랭킹 등
- **리스크 관리**: 포지션 사이즈, 단일 종목 한도, 일일 손실 제한 내장
- **Dry-run 모드**: 기본값은 dry-run (실제 주문 없이 시뮬레이션)
- **Rate Limit 처리**: 429 자동 재시도 + 지수 백오프 + jitter
- **국내/미국 주식 모두 지원**

## 설치

```bash
pip install -r requirements.txt
```

## 설정

1. `config.example.yaml`을 `config.yaml`으로 복사
2. 토스증션 Open API 키 입력
3. `account_id` 설정 (미설정 시 첫 번째 계좌 자동 선택)

```bash
cp config.example.yaml config.yaml
# config.yaml 편집...
```

## 실행

### 한 번 실행
```bash
python scripts/run_once.py
```

### 루프 실행 (장시간)
```bash
python scripts/run_loop.py
```

## 전략 추가

`src/strategy/base.py`의 `BaseStrategy`를 상속하세요:

```python
from src.strategy.base import BaseStrategy, StrategyContext, Signal

class MyStrategy(BaseStrategy):
    def name(self) -> str:
        return "My Strategy"
    
    def evaluate(self, context: StrategyContext) -> list[Signal]:
        signals = []
        # 시장 데이터 분석 로직...
        # Signal(symbol="005930", side="BUY", quantity=10, order_type="LIMIT", price=80000, reason="...")
        return signals
```

`config.yaml`의 `strategies`에 경로를 추가하면 됩니다.

## Dry-run ↔ 실전

```yaml
engine:
  dry_run: true   # ← 시뮬레이션 모드 (기본값, 안전)
  dry_run: false  # ← 실전 주문 모드
```

## API Rate Limit

| 그룹 | TPS |
|------|-----|
| ORDER | 10/s |
| ORDER_HISTORY | 5/s |
| ORDER_INFO | 6/s |
| MARKET_DATA | 15/s |
| MARKET_DATA_CHART | 20/s |
| STOCK | 5/s |

자동 재시도 처리되지만, 너무 잦은 호출은 피하세요.

## 프로젝트 구조

```
├── README.md
├── requirements.txt
├── config.example.yaml
├── src/
│   ├── client.py          # 토스증권 Open API 클라이언트
│   ├── config.py          # 설정 로더
│   ├── models.py          # 데이터 모델
│   ├── engine.py          # 메인 트레이딩 엔진
│   ├── risk.py            # 리스크 관리
│   ├── notify.py          # 알림
│   ├── utils.py           # 유틸리티
│   └── strategy/
│       └── base.py        # BaseStrategy
├── strategies/
│   └── example_strategy.py
└── scripts/
    ├── run_once.py
    └── run_loop.py
```

## License

MIT
