#!/bin/sh
# На VM: состояние бота (deploy/deploy.sh кладёт его в ~/solar/deploy). Запуск с компьютера:
#   gcloud compute ssh solar-bot --zone us-east1-b --command "sh solar/deploy/status.sh"
cd "$HOME/solar"
echo "bot: $(systemctl is-active solar-bot)"
journalctl -u solar-bot -n 15 --no-pager
systemctl list-timers 'solar*' --no-pager
free -m | sed -n 2p
