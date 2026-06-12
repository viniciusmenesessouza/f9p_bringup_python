#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install -y python3 python3-venv python3-pip

# Avoid leaking ROS/colcon Python paths into this standalone venv install.
unset PYTHONPATH
unset AMENT_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset CMAKE_PREFIX_PATH

python3 -m venv .venv

. .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install typeguard
python -m pip install -r requirements.txt

sudo usermod -a -G dialout "$USER"

if systemctl list-unit-files | grep -q '^ModemManager.service'; then
    sudo systemctl stop ModemManager || true
    sudo systemctl disable ModemManager || true
fi

echo
echo "Installation complete."
echo "User added to dialout for serial access."
echo "Log out and log back in before using /dev/ttyUSB* or /dev/ttyACM*."
echo
echo "Test imports with:"
echo "  . .venv/bin/activate"
echo "  python -c \"import serial, pyubx2, flask, yaml; print('ok')\""
echo
echo "Run with:"
echo "  ./run_ubuntu.sh"