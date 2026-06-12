#!/usr/bin/env bash
set -euo pipefail

SUDO=''
if (( EUID != 0 )); then
    SUDO='sudo'
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

$SUDO cp "$SCRIPT_DIR/99-ublox-gps.rules" /etc/udev/rules.d/99-ublox-gps.rules
$SUDO udevadm control --reload-rules
$SUDO udevadm trigger