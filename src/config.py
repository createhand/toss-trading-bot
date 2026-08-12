"""설정 로더 — config.yaml 또는 환경변수에서 설정을 읽어옵니다."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ApiConfig:
    client_id: str = ""
    client_secret: str = ""
    account_id: str | None = None


@dataclass
class EngineConfig:
    dry_run: bool = True
    interval_seconds: int = 60
    watch_symbols: list[str] = field(default_factory=list)


@dataclass
class RiskConfig:
    max_position_pct: float = 30.0
    max_single_stock_pct: float = 20.0
    daily_loss_limit_pct: float = 5.0


@dataclass
class NotifyConfig:
    log_to_stdout: bool = True
    webhook_url: str | None = None


@dataclass
class DbConfig:
    host: str = "192.168.29.200"
    port: int = 5432
    database: str = "mysvc"
    user: str = "postgres"
    password: str = ""


@dataclass
class FlaskConfig:
    host: str = "0.0.0.0"
    port: int = 8081


@dataclass
class AppConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategies: list[str] = field(default_factory=list)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    db: DbConfig = field(default_factory=DbConfig)
    flask: FlaskConfig = field(default_factory=FlaskConfig)


def load_config(path: str | Path | None = None) -> AppConfig:
    """YAML 설정 파일에서 설정을 로드합니다.

    환경변수로 덮어쓰기 가능:
      TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_ACCOUNT_ID
      TOSS_DRY_RUN (true/false)
    """
    path = Path(path) if path else Path("config.yaml")
    cfg = AppConfig()

    if path.exists():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        api = raw.get("api", {})
        cfg.api = ApiConfig(
            client_id=os.getenv("TOSS_CLIENT_ID", api.get("client_id", "")),
            client_secret=os.getenv("TOSS_CLIENT_SECRET", api.get("client_secret", "")),
            account_id=os.getenv("TOSS_ACCOUNT_ID", api.get("account_id")),
        )

        eng = raw.get("engine", {})
        cfg.engine = EngineConfig(
            dry_run=os.getenv("TOSS_DRY_RUN", str(eng.get("dry_run", True))).lower() == "true",
            interval_seconds=int(eng.get("interval_seconds", 60)),
            watch_symbols=eng.get("watch_symbols", []),
        )

        risk = raw.get("risk", {})
        cfg.risk = RiskConfig(
            max_position_pct=float(risk.get("max_position_pct", 30.0)),
            max_single_stock_pct=float(risk.get("max_single_stock_pct", 20.0)),
            daily_loss_limit_pct=float(risk.get("daily_loss_limit_pct", 5.0)),
        )

        cfg.strategies = raw.get("strategies", [])

        notify = raw.get("notify", {})
        cfg.notify = NotifyConfig(
            log_to_stdout=notify.get("log_to_stdout", True),
            webhook_url=notify.get("webhook_url"),
        )

        db_raw = raw.get("db", {})
        cfg.db = DbConfig(
            host=db_raw.get("host", "192.168.29.200"),
            port=int(db_raw.get("port", 5432)),
            database=db_raw.get("database", "mysvc"),
            user=db_raw.get("user", "postgres"),
            password=os.getenv("DB_PASSWORD", db_raw.get("password", "")),
        )

        flask_raw = raw.get("api_server", {})
        cfg.flask = FlaskConfig(
            host=flask_raw.get("host", "0.0.0.0"),
            port=int(flask_raw.get("port", 8081)),
        )

    # 환경변수 최종 덮어쓰기
    if os.getenv("TOSS_CLIENT_ID"):
        cfg.api.client_id = os.getenv("TOSS_CLIENT_ID")
    if os.getenv("TOSS_CLIENT_SECRET"):
        cfg.api.client_secret = os.getenv("TOSS_CLIENT_SECRET")
    if os.getenv("TOSS_ACCOUNT_ID"):
        cfg.api.account_id = os.getenv("TOSS_ACCOUNT_ID")

    return cfg
