# f9p_bringup_python

Standalone Python helper for reading a u-blox GNSS receiver over serial, optionally feeding RTCM corrections from an NTRIP caster, and viewing the live position in a browser-based satellite map.

The main entry point is `ublox_live_satmap.py`. It starts a Flask web server, reads UBX/NMEA data from the configured serial port, and exposes the latest fix at `/position`.

## Requirements

- Python 3
- A u-blox GNSS receiver connected over USB or serial
- Linux, Windows, or another OS supported by `pyserial`
- Internet access for map tiles in the browser

Python dependencies are listed in `requirements.txt`:

- `pyserial`
- `pyubx2`
- `Flask`
- `PyYAML`

## Ubuntu Setup

Run the installer:

```bash
./install_ubuntu.sh
```

This creates `.venv`, installs the Python dependencies, adds the current user to the `dialout` group, and disables `ModemManager` if it is present.

After installation, log out and back in so the `dialout` group change takes effect.

If you want a stable device name such as `/dev/ttyF9P`, install the udev rule:

```bash
./udev/install_udev_rule.sh
```

Then unplug and reconnect the receiver.

## Windows Setup

Run:

```powershell
.\install_windows.ps1
```

This creates `.venv` and installs the Python dependencies.

## Configuration

Edit `config.yaml` before running.

The most important settings are:

```yaml
serial:
  port: /dev/ttyF9P
  baud: 115200
  timeout: 1.0

server:
  host: 127.0.0.1
  port: 5000
```

On Windows, set `serial.port` to a COM port such as:

```yaml
serial:
  port: COM5
```

NTRIP correction input is controlled by the `ntrip` section:

```yaml
ntrip:
  enabled: true
  host: example.com
  port: 2101
  mountpoint: MOUNT
  username: your_username
  password: your_password
  tls: false
```

Set `ntrip.enabled` to `false` if you only want to read the receiver position without injecting RTCM corrections.

## Running

On Ubuntu:

```bash
./run_ubuntu.sh
```

Or run the script directly:

```bash
. .venv/bin/activate
python ublox_live_satmap.py --config config.yaml
```

On Windows:

```powershell
.\.venv\Scripts\python.exe .\ublox_live_satmap.py --config .\config.yaml
```

After startup, open the URL printed by the program. By default:

```text
http://127.0.0.1:5000
```

The map starts at world view and jumps to the first valid GNSS fix.

## Browser Views

- `/` shows the live satellite map.
- `/position` returns the latest position as JSON.

The map displays latitude/longitude, MSL altitude, ellipsoid height, fix type, RTK carrier state, satellite count, horizontal/vertical accuracy, speed, heading, pDOP, message type, and timestamp.

## Troubleshooting

If the receiver cannot be opened:

- Check that `serial.port` in `config.yaml` matches the actual device.
- On Ubuntu, confirm the user is in the `dialout` group after logging out and back in.
- If using udev rules, reconnect the receiver after installing the rule.
- Make sure no other program is using the same serial port.

If the map opens but does not move:

- Wait for the receiver to produce a valid fix.
- Confirm the receiver is outputting UBX `NAV-PVT` or NMEA position messages.
- Check the terminal output for `[serial]`, `[reader]`, `[ntrip]`, or `[fix]` messages.

If NTRIP does not connect:

- Verify `host`, `port`, `mountpoint`, `username`, and `password`.
- Check whether the caster requires TLS and set `ntrip.tls` accordingly.
- Confirm the receiver has a valid position, because GGA messages are sent to the caster only after a valid fix is available.

## Notes

- Keep private NTRIP credentials out of shared copies of `config.yaml`.
- The application is intended as a standalone utility and does not require ROS.
- The browser needs network access to load Leaflet and satellite map tiles from external CDNs.
