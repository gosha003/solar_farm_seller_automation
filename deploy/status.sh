#!/bin/sh
# On the VM: bot status (deploy/deploy.sh puts it in ~/solar/deploy). Run from the PC:
#   gcloud compute ssh solar-bot --zone us-east1-b --command "sh solar/deploy/status.sh"
cd "$HOME/solar"
echo "bot: $(systemctl is-active solar-bot)"
journalctl -u solar-bot -n 15 --no-pager
systemctl list-timers 'solar*' --no-pager
free -m | sed -n 2p
