#!/bin/sh
# From the owner's PC (Git Bash): committed code + config.local.yaml + .env -> VM, install/update.
#   deploy/deploy.sh            code and .env
#   deploy/deploy.sh --code     code only (leave .env on the VM untouched)
set -e
VM=solar-bot ZONE=us-east1-b
G=$(command -v gcloud.cmd || command -v gcloud)  # Windows: gcloud.cmd
cd "$(dirname "$0")/.."
LOCAL=solar_outreach/config.local.yaml
[ -f $LOCAL ] || { echo "$LOCAL is missing: the VM would run on the public example config" >&2; exit 1; }
O="--zone $ZONE --quiet --strict-host-key-checking=no"
git archive --format=tar.gz -o solar.tgz HEAD
tr -d '\r' < deploy/setup.sh > setup.sh  # the Windows working copy has CRLF: sh on the VM fails on `set -e`
"$G" compute scp $O solar.tgz setup.sh $LOCAL $VM:
rm solar.tgz setup.sh
[ "$1" = --code ] || "$G" compute scp $O .env $VM:solar.env
"$G" compute ssh $O $VM --command "sh ~/setup.sh"
