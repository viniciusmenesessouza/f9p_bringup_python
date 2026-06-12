#!/usr/bin/env bash
set -e

. .venv/bin/activate
python ublox_live_satmap.py --config config.yaml