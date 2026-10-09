#!/bin/sh
# На VM (Debian 12): распаковать код, venv, systemd. Повторный запуск = обновление.
set -e
DIR="$HOME/solar"
sudo timedatectl set-timezone America/Toronto      # даты в таблице и дайджест по времени владельца
python3 -c "import ensurepip" 2>/dev/null || { sudo apt-get update -qq; sudo apt-get install -y -qq python3-venv; }
if [ ! -f /swapfile ]; then   # 1 ГБ RAM мало для бота и поиска одновременно: swap 2 ГБ на бесплатном диске
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi
mkdir -p "$DIR"
tar -xzf "$HOME/solar.tgz" -C "$DIR" && rm "$HOME/solar.tgz"
[ -f "$HOME/solar.env" ] && mv "$HOME/solar.env" "$DIR/.env"
chmod 600 "$DIR/.env"
[ -x "$DIR/.venv/bin/pip" ] || { rm -rf "$DIR/.venv"; python3 -m venv "$DIR/.venv"; }
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
for f in "$DIR"/deploy/*.service "$DIR"/deploy/*.timer; do
  sed "s#__USER__#$USER#g; s#__DIR__#$DIR#g" "$f" | sudo tee "/etc/systemd/system/$(basename "$f")" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable -q solar-bot solar-search-sites.timer solar-search-web.timer
sudo systemctl restart solar-bot
sudo systemctl start solar-search-sites.timer solar-search-web.timer
systemctl --no-pager --lines=5 status solar-bot
