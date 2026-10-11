#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"

# Copy only release source/docs/scripts/metadata; preserve Pi settings and credentials.
ssh pi@192.168.1.127 'mkdir -p /home/pi/phos/src /home/pi/phos/config /home/pi/phos/docs /home/pi/phos/deploy /home/pi/phos/scripts'
rsync -av --delete --exclude '__pycache__' ./src/ pi@192.168.1.127:/home/pi/phos/src/
rsync -av --delete --exclude '__pycache__' ./scripts/ pi@192.168.1.127:/home/pi/phos/scripts/
rsync -av ./pyproject.toml ./requirements-web.txt ./requirements.txt ./README.md pi@192.168.1.127:/home/pi/phos/
rsync -av ./deploy/ pi@192.168.1.127:/home/pi/phos/deploy/
rsync -av ./docs/ pi@192.168.1.127:/home/pi/phos/docs/
# Seed YAML only for a fresh deployment. Existing YAML, YML, or legacy JSON is
# left untouched so ConfigRepository retains the user's active source.
if ssh pi@192.168.1.127 'test ! -e /home/pi/phos/config/phos.yaml && test ! -e /home/pi/phos/config/phos.yml && test ! -e /home/pi/phos/config/phos.json'; then
    rsync -av ./config/phos.yaml pi@192.168.1.127:/home/pi/phos/config/
fi
