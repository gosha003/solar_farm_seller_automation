#!/bin/sh
# С компьютера владельца (Git Bash): закоммиченный код + .env -> VM, установка/обновление.
#   deploy/deploy.sh            код и .env
#   deploy/deploy.sh --code     только код (.env на VM не трогать)
set -e
VM=solar-bot ZONE=us-east1-b
cd "$(dirname "$0")/.."
git archive --format=tar.gz -o "${TMP:-/tmp}/solar.tgz" HEAD
gcloud compute scp --zone $ZONE "${TMP:-/tmp}/solar.tgz" deploy/setup.sh $VM:~
[ "$1" = --code ] || gcloud compute scp --zone $ZONE .env $VM:~/solar.env
gcloud compute ssh --zone $ZONE $VM --command "sh ~/setup.sh"
