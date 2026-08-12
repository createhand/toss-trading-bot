#!/bin/bash
# 토스 트레이딩 봇 설치 스크립트
# sudo install.sh

set -e

INSTALL_DIR="/opt/toss-trading-bot"
SERVICE_USER="deploy"
REPO_URL="https://github.com/createhand/toss-trading-bot.git"

echo "=== 토스 트레이딩 봇 설치 ==="

# 1. 배포 사용자 확인
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "⚠️  유저 $SERVICE_USER 없음. 생성합니다..."
    useradd -m -s /bin/bash "$SERVICE_USER"
fi

# 2. 의존성 설치
echo "📦 시스템 의존성 설치..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git > /dev/null

# 3. repo clone
if [ -d "$INSTALL_DIR" ]; then
    echo "📂 기존 디렉토리 발견 → pull"
    cd "$INSTALL_DIR" && git pull
else
    echo "📂 repo clone..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# 4. venv + pip install
echo "🐍 Python venv 생성 + 패키지 설치..."
cd "$INSTALL_DIR"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

# 5. config.yaml 생성 (없으면 예시 복사)
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo ""
    echo "⚠️  config.yaml을 편집하세요!"
    echo "   vi $INSTALL_DIR/config.yaml"
    echo ""
fi

# 6. DB 스키마 생성 (psql 있을 때만)
if command -v psql &>/dev/null && [ -f db/schema.sql ]; then
    echo "🗄️  DB 스키마 생성 (db/schema.sql)..."
    # 비밀번호는 서비스 파일에서 환경변수로 주입됨
    PGPASSWORD="do4#Dk!Ekk33d#zz0l.,##" psql -h 192.168.29.200 -U postgres -d mysvc -f db/schema.sql 2>/dev/null || \
        echo "⚠️  DB 스키마 수동 실행 필요: psql -h 192.168.29.200 -U postgres -d mysvc -f db/schema.sql"
else
    echo "⚠️  DB 스키마를 수동으로 실행하세요: psql -h 192.168.29.200 -U postgres -d mysvc -f db/schema.sql"
fi

# 7. systemd 서비스 설치
echo "⚙️  systemd 서비스 설치..."
cp scripts/toss-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable toss-bot

echo ""
echo "=== 설치 완료 ==="
echo ""
echo "📋 다음 단계:"
echo "   1. config.yaml 편집:  vi $INSTALL_DIR/config.yaml"
echo "   2. 봇 시작:           sudo systemctl start toss-bot"
echo "   3. 봇 상태:           sudo systemctl status toss-bot"
echo "   4. 봇 로그:           sudo journalctl -u toss-bot -f"
echo "   5. 봇 정지:           sudo systemctl stop toss-bot"
echo "   6. 봇 재시작:         sudo systemctl restart toss-bot"
echo ""
