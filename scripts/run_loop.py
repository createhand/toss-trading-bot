"""주기적 루프 실행 — 장시간 동안 계속 실행"""

import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.engine import TradingEngine


def main():
    config = load_config()

    if not config.api.client_id or not config.api.client_secret:
        print("❌ API 키가 설정되지 않았습니다.")
        print("config.yaml을 생성하고 api.client_id / api.client_secret을 설정하세요.")
        print("  cp config.example.yaml config.yaml")
        sys.exit(1)

    engine = TradingEngine(config)
    engine.initialize()
    engine.run_loop()


if __name__ == "__main__":
    main()
