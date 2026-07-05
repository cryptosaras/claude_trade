#!/usr/bin/env bash
# First-time VPS setup. Run as root on the VPS:
#   bash <(curl -fsSL https://raw.githubusercontent.com/cryptosaras/claude_trade/main/ops/deploy.sh)
# or: git clone https://github.com/cryptosaras/claude_trade.git /opt/claude_trade && bash /opt/claude_trade/ops/deploy.sh
set -euo pipefail

DIR=/opt/claude_trade
if [ ! -d "$DIR/.git" ]; then
  git clone https://github.com/cryptosaras/claude_trade.git "$DIR"
fi
cd "$DIR"

if [ ! -f .env ]; then
  {
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
    echo "DASH_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=')"
    echo "TZ=UTC"
  } > .env
  echo "generated .env:"
  cat .env
fi

docker compose up -d --build

# autopull every 2 minutes
chmod +x ops/autopull.sh
CRON="*/2 * * * * /opt/claude_trade/ops/autopull.sh >/dev/null 2>&1"
( crontab -l 2>/dev/null | grep -v claude_trade/ops/autopull ; echo "$CRON" ) | crontab -

echo "deployed. Dashboard: http://$(curl -s ifconfig.me):8420  (user: trader)"
