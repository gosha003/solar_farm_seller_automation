#!/bin/sh
# На VM: состояние бота (deploy/deploy.sh кладёт его в ~/solar/deploy). Запуск с компьютера:
#   gcloud compute ssh solar-bot --zone us-east1-b --command "sh solar/deploy/status.sh"
cd "$HOME/solar"
echo "bot: $(systemctl is-active solar-bot)"
journalctl -u solar-bot -n 15 --no-pager
systemctl list-timers 'solar*' --no-pager
.venv/bin/python -c 'from solar_outreach import budget; print("pubsub:", budget.spent_ratio("projects/solar-bg/subscriptions/billing-alerts-vm"))'
free -m | sed -n 2p
