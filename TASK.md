# TASK: 토스증권 Open API 기반 트레이딩 봇 소스베이스

## 목표
토스증권 Open API를 활용한 트레이딩 봇의 기본 소스베이스를 구축하세요.
전략은 나중에 유연하게 추가/교체할 수 있게 **전략 패턴(Strategy Pattern)**으로 구조화합니다.

## 요구사항

### 1. 프로젝트 구조
```
toss-trading-bot/
├── README.md              # 프로젝트 설명, 설정 방법, 사용법
├── requirements.txt       # 의존성 (requests, python-dotenv, pyyaml)
├── config.example.yaml   # 설정 예시 파일
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── config.py          # YAML 설정 로더 (API key, account, dry-run 모드 등)
│   ├── client.py          # 토스증권 Open API 클라이언트 (인증, 시세, 주문 전부)
│   ├── models.py          # 데이터 모델 (Candle, Order, Position, Signal 등)
│   ├── strategy/
│   │   ├── __init__.py
│   │   └── base.py        # BaseStrategy 추상 클래스
│   ├── engine.py          # 메인 엔진 (루프, 시그널 수집 → 전략 실행 → 주문)
│   ├── risk.py            # 리스크 관리 (포지션 사이즈, 최대 손실, 일일 한도)
│   ├── notify.py          # 알림 (stdout 로그 + 확장 가능한 hook)
│   └── utils.py           # 유틸 (시간, 포맷팅, rate limit 등)
├── strategies/            # 사용자 정의 전략 예시 (git에 포함)
│   └── example_strategy.py  # 간단한 예시 전략 (빈骨架)
└── scripts/
    ├── run_once.py        # 한 번 실행하고 종료
    └── run_loop.py        # 주기적 루프 실행 (장시간 동안)
```

### 2. 토스증권 Open API 클라이언트 (`src/client.py`)
기존 toss_client.py 코드를 참고하되, 전체 API를 커버하세요.

**인증**: Client Credentials Grant → POST /oauth2/token
- 토큰 자동 갱신 (만료 60초 전 리프레시)

**시계열 데이터** (Market Data):
- `GET /api/v1/prices` — 현재가 (다중 종목)
- `GET /api/v1/orderbook` — 호가
- `GET /api/v1/trades` — 체결
- `GET /api/v1/candles` — 캔들 (1m, 1d)
- `GET /api/v1/price-limits` — 상하한가

**종목 정보** (Stock Info):
- `GET /api/v1/stocks` — 종목 기본 정보
- `GET /api/v1/stocks/{symbol}/warnings` — 매수 유의사항
- `GET /api/v1/stocks/{symbol}/investor-trading` — 투자자별 매매동향
- `GET /api/v1/stocks/{symbol}/program-trades` — 프로그램매매 동향
- `GET /api/v1/stocks/{symbol}/short-selling` — 공매도 동향
- `GET /api/v1/stocks/{symbol}/credit-trades` — 신용거래 동향
- `GET /api/v1/stocks/{symbol}/securities-lending` — 대차거래 동향

**계좌/자산** (Account · Asset):
- `GET /api/v1/accounts` — 계좌 목록 (헤더: X-Tossinvest-Account)
- `GET /api/v1/holdings` — 보유 주식

**주문** (Order):
- `POST /api/v1/orders` — 주문 생성 (지정가 LIMIT, 시장가 MARKET)
  - body: { symbol, side: "BUY"|"SELL", orderType: "LIMIT"|"MARKET", quantity, price (LIMIT시), timeInForce: "DAY", clientOrderId (멱등), confirmHighValueOrder }
- `POST /api/v1/orders/{orderId}/modify` — 주문 정정
- `POST /api/v1/orders/{orderId}/cancel` — 주문 취소
- `GET /api/v1/orders` — 주문 목록 (status 파라미터: open|closed)
- `GET /api/v1/orders/{orderId}` — 주문 상세

**거래 가능 정보** (Order Info):
- `GET /api/v1/buying-power` — 매수 가능 금액
- `GET /api/v1/sellable-quantity` — 판매 가능 수량
- `GET /api/v1/commissions` — 수수료

**조건주문** (Conditional Order):
- `POST /api/v1/conditional-orders` — 조건주문 등록 (SINGLE/OCO/OTO)
- `GET /api/v1/conditional-orders` — 조건주문 목록
- `POST /api/v1/conditional-orders/{id}/modify` — 수정
- `DELETE /api/v1/conditional-orders/{id}` — 취소

**기타**:
- `GET /api/v1/exchange-rate` — 환율
- `GET /api/v1/market-calendar/KR` — 국내 장 운영 정보
- `GET /api/v1/rankings` — 랭킹

Rate limit 처리:
- 429 수신 시 `Retry-After` 헤더값 대기 후 재시도
- 지수 백오프 + jitter 적용
- 모든 응답에서 `X-RateLimit-Remaining` 로 선제적 속도 조절

### 3. 전략 패턴 (`src/strategy/base.py`)
```python
class BaseStrategy(ABC):
    @abstractmethod
    def name(self) -> str: ...
    
    @abstractmethod
    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """시장 데이터와 보유 상태를 분석해 매수/매도 시그널 반환"""
        ...
    
    def on_fill(self, fill: Fill):
        """주문 체결 시 콜백 (선택적 오버라이드)"""
        pass
```

- `StrategyContext`: 종목 데이터(candles, prices, orderbook), 보유 포지션, 매수가능금액 등
- `Signal`: { symbol, side(BUY/SELL), quantity, order_type(LIMIT/MARKET), price(optional), reason }

### 4. 리스크 관리 (`src/risk.py`)
- 최대 포지션 크기 제한 (총 자산 대비 %)
- 단일 종목 최대 투자 비중
- 일일 최대 손실 한도 (drawdown circuit breaker)
- 주문 전 필터 (리스크 규칙 위반 시 주문 차단)

### 5. 엔진 (`src/engine.py`)
- 주기 루프 (configurable interval, 기본 60초)
- 장시간 체크 (09:00~15:30 KST)
- 각 루프: 시장 데이터 수집 → 전략 평가 → 리스크 필터 → 주문 실행 → 체결 확인
- Dry-run 모드: 주문은 실제로 넣지 않고 로그만 남김
- Graceful shutdown (SIGINT/SIGTERM)

### 6. 설정 (`config.example.yaml`, `src/config.py`)
```yaml
# API
api:
  client_id: "your_client_id"
  client_secret: "your_client_secret"
  account_id: null  # null이면 첫 번째 계좌 자동 선택

# 엔진
engine:
  dry_run: true          # 기본값 true (안전)
  interval_seconds: 60  # 루프 간격
  watch_symbols:
    - "005930"  # 삼성전자
    - "000660"  # SK하이닉스
    - "035420"  # NAVER

# 리스크
risk:
  max_position_pct: 30       # 총 자산 대비 최대 포지션 비율 (%)
  max_single_stock_pct: 20   # 단일 종목 최대 비중 (%)
  daily_loss_limit_pct: 5   # 일일 최대 손실 한도 (%)

# 전략
strategies:
  - strategies.example_strategy.ExampleStrategy

# 알림
notify:
  log_to_stdout: true
  # webhook_url: null  # 추후 확장
```

### 7. 실행 스크립트
- `scripts/run_once.py`: 한 번 실행 (evaluate → 결과 출력/주문 → 종료)
- `scripts/run_loop.py`: 장시간 동안 루프 실행

### 8. README.md
- 프로젝트 개요
- 설치 방법 (pip install -r requirements.txt)
- 설정 방법 (config.yaml 복사, API 키 입력)
- 실행 방법 (run_once / run_loop)
- 전략 추가 방법 (BaseStrategy 상속)
- Dry-run / 실전 모드 설명
- API Rate Limit 안내

## 코드 스타일
- Python 3.11+ (type hints 활용)
- 한국어 코멘트 허용
- docstring은 영문
- 함수/메서드는 명확하게 한 가지 역할
- 에러 처리 철저히 (API 에러, 네트워크 에러, 설정 에러)

## IMPORTANT
- secrets(API key 등)는 코드에 하드코딩하지 마세요. config.yaml이나 .env로 관리
- .gitignore에 config.yaml, .env, __pycache__ 포함
- 모든 주문은 기본적으로 dry_run=true로 동작하게 만드세요
- 실전 모드 전환은 명시적 설정으로만 가능하게
