#!/bin/sh
# С компьютера владельца (Git Bash): закоммиченный код + .env -> VM, установка/обновление.
#   deploy/deploy.sh            код и .env
#   deploy/deploy.sh --code     только код (.env на VM не трогать)
set -e
VM=solar-bot ZONE=us-east1-b
G=$(command -v gcloud.cmd || command -v gcloud)  # Windows: gcloud.cmd
cd "$(dirname "$0")/.."
O="--zone $ZONE --quiet --strict-host-key-checking=no"
git archive --format=tar.gz -o solar.tgz HEAD
"$G" compute scp $O solar.tgz deploy/setup.sh $VM:
rm solar.tgz
[ "$1" = --code ] || "$G" compute scp $O .env $VM:solar.env
"$G" compute ssh $O $VM --command "sh ~/setup.sh"
