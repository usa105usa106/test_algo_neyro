#!/usr/bin/env bash
set -e
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Edit TELEGRAM_BOT_TOKEN and ADMIN_TELEGRAM_ID before running."
fi
mkdir -p storage/{candles,charts,meta,exports,logs,state,secrets,work}
echo "Install complete. Run: source venv/bin/activate && python run.py"
