#!/bin/sh
# On the VM (Debian 12): unpack code, venv, systemd units. Running it again = update.
set -e
DIR="$HOME/solar"
sudo timedatectl set-timezone America/Toronto      # sheet dates and the digest follow the owner's time zone
python3 -c "import ensurepip" 2>/dev/null || { sudo apt-get update -qq; sudo apt-get install -y -qq python3-venv; }
if [ ! -f /swapfile ]; then   # 1 GB RAM is too little for bot + search at once: 2 GB swap on the free disk
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi
mkdir -p "$DIR"
tar -xzf "$HOME/solar.tgz" -C "$DIR" && rm "$HOME/solar.tgz"
[ -f "$HOME/solar.env" ] && mv "$HOME/solar.env" "$DIR/.env"
[ -f "$HOME/config.local.yaml" ] && mv "$HOME/config.local.yaml" "$DIR/solar_outreach/"
chmod 600 "$DIR/.env"
[ -x "$DIR/.venv/bin/pip" ] || { rm -rf "$DIR/.venv"; python3 -m venv "$DIR/.venv"; }
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
for f in "$DIR"/deploy/*.service "$DIR"/deploy/*.timer; do
  sed "s#__USER__#$USER#g; s#__DIR__#$DIR#g" "$f" | sudo tee "/etc/systemd/system/$(basename "$f")" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable -q solar-bot solar-search-sites.timer solar-search-web.timer solar-search-brokers.timer solar-search-regions.timer solar-brief.timer
sudo systemctl restart solar-bot
sudo systemctl start solar-search-sites.timer solar-search-web.timer solar-search-brokers.timer solar-search-regions.timer solar-brief.timer
systemctl --no-pager --lines=5 status solar-bot
