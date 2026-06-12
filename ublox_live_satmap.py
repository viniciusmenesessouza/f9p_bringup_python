#!/usr/bin/env python3
"""
Read u-blox GNSS messages from a serial COM port, optionally inject RTCM
corrections from an NTRIP caster, and show live position on a satellite map.

No initial GNSS position is configured.
The map opens at world view and jumps to the first real receiver fix.

Run:
    python ublox_live_satmap.py --config config.yaml
"""

import argparse
import base64
import socket
import ssl
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import serial
import yaml
from flask import Flask, jsonify, render_template_string
from pyubx2 import UBXReader


latest_position = {
    "valid": False,
    "lat": None,
    "lon": None,
    "height_ellipsoid_m": None,
    "alt_msl_m": None,
    "hacc_m": None,
    "vacc_m": None,
    "speed_mps": None,
    "speed_acc_mps": None,
    "heading_deg": None,
    "heading_acc_deg": None,
    "pdop": None,
    "fix_type": None,
    "fix_label": None,
    "gnss_fix_ok": None,
    "diff_soln": None,
    "carrier_solution": None,
    "carrier_label": None,
    "num_sv": None,
    "msg": None,
    "timestamp": None,
}

lock = threading.Lock()
stop_event = threading.Event()
app = Flask(__name__)
CFG = {}


DEFAULT_CONFIG = {
    "serial": {
        "port": "COM5",
        "baud": 38400,
        "timeout": 1.0,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 5000,
    },
    "map": {
        "initial_zoom": 2,
        "max_zoom": 20,
        "first_fix_zoom": {
            "hacc_over_50": 16,
            "hacc_over_15": 17,
            "hacc_over_5": 18,
            "default": 19,
        },
        "path_max_points": 2000,
        "update_interval_ms": 250,
    },
    "ntrip": {
        "enabled": False,
        "host": "",
        "port": 2101,
        "mountpoint": "",
        "username": "",
        "password": "",
        "tls": False,
        "timeout_s": 10.0,
        "reconnect_s": 5.0,
        "gga_interval_s": 10.0,
    },
}


HTML = r"""
<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>u-blox Live Satellite Map</title>

    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

    <style>
        html, body {
            height: 100%;
            margin: 0;
            font-family: Arial, sans-serif;
        }

        #map {
            height: 100%;
            width: 100%;
        }

        #panel {
            position: absolute;
            top: 12px;
            right: 12px;
            z-index: 1000;
            background: rgba(255,255,255,0.93);
            padding: 12px 14px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.25);
            min-width: 300px;
            font-size: 14px;
            line-height: 1.35;
        }

        #status {
            font-weight: bold;
            margin-bottom: 6px;
        }

        .small {
            font-size: 12px;
            color: #444;
        }
    </style>
</head>

<body>
<div id="map"></div>

<div id="panel">
    <div id="status">Waiting for GNSS fix...</div>
    <div id="latlon">lat/lon: —</div>
    <div id="alt">alt MSL: —</div>
    <div id="height">height ellipsoid: —</div>
    <div id="fix">fix: —</div>
    <div id="rtk">RTK: —</div>
    <div id="sv">satellites: —</div>
    <div id="hacc">hAcc: —</div>
    <div id="vacc">vAcc: —</div>
    <div id="speed">speed: —</div>
    <div id="heading">heading: —</div>
    <div id="pdop">pDOP: —</div>
    <div class="small" id="msg">msg: —</div>
    <div class="small" id="time">time: —</div>
</div>

<script>
    const mapConfig = {{ map_config | tojson }};

    const map = L.map("map", { zoomControl: true });

    L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        {
            maxZoom: mapConfig.max_zoom,
            attribution: "Tiles © Esri"
        }
    ).addTo(map);

    map.fitWorld();

    let marker = null;
    let path = null;
    let accCircle = null;
    let points = [];
    let firstFix = true;

    function fmt(x, digits = 7) {
        if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
        return Number(x).toFixed(digits);
    }

    function fmtUnit(x, digits, unit) {
        if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
        return `${Number(x).toFixed(digits)} ${unit}`;
    }

    function presentableZoom(hacc) {
        const z = mapConfig.first_fix_zoom;

        if (hacc === null || hacc === undefined) return z.default;

        hacc = Number(hacc);

        if (hacc > 50) return z.hacc_over_50;
        if (hacc > 15) return z.hacc_over_15;
        if (hacc > 5) return z.hacc_over_5;

        return z.default;
    }

    async function updatePosition() {
        try {
            const r = await fetch("/position", { cache: "no-store" });
            const p = await r.json();

            if (!p.valid) {
                document.getElementById("status").innerText = "Waiting for GNSS fix...";
                return;
            }

            const lat = Number(p.lat);
            const lon = Number(p.lon);
            const ll = [lat, lon];

            document.getElementById("status").innerText = "Live GNSS position";
            document.getElementById("latlon").innerText = `lat/lon: ${fmt(lat)}, ${fmt(lon)}`;
            document.getElementById("alt").innerText = `alt MSL: ${fmtUnit(p.alt_msl_m, 3, "m")}`;
            document.getElementById("height").innerText = `height ellipsoid: ${fmtUnit(p.height_ellipsoid_m, 3, "m")}`;
            document.getElementById("fix").innerText =
                `fix: ${p.fix_label ?? "—"} | ok: ${p.gnss_fix_ok}`;
            document.getElementById("rtk").innerText =
                `RTK: ${p.carrier_label ?? "—"} | diff: ${p.diff_soln}`;
            document.getElementById("sv").innerText = `satellites: ${p.num_sv ?? "—"}`;
            document.getElementById("hacc").innerText = `hAcc: ${fmtUnit(p.hacc_m, 3, "m")}`;
            document.getElementById("vacc").innerText = `vAcc: ${fmtUnit(p.vacc_m, 3, "m")}`;
            document.getElementById("speed").innerText =
                `speed: ${fmtUnit(p.speed_mps, 3, "m/s")} | acc: ${fmtUnit(p.speed_acc_mps, 3, "m/s")}`;
            document.getElementById("heading").innerText =
                `heading: ${fmtUnit(p.heading_deg, 5, "deg")} | acc: ${fmtUnit(p.heading_acc_deg, 5, "deg")}`;
            document.getElementById("pdop").innerText = `pDOP: ${fmt(p.pdop, 2)}`;
            document.getElementById("msg").innerText = `msg: ${p.msg ?? "—"}`;
            document.getElementById("time").innerText = `time: ${p.timestamp ?? "—"}`;

            if (marker === null) {
                marker = L.marker(ll).addTo(map);
            } else {
                marker.setLatLng(ll);
            }

            if (p.hacc_m !== null && Number(p.hacc_m) > 0) {
                if (accCircle === null) {
                    accCircle = L.circle(ll, {
                        radius: Number(p.hacc_m),
                        weight: 2
                    }).addTo(map);
                } else {
                    accCircle.setLatLng(ll);
                    accCircle.setRadius(Number(p.hacc_m));
                }
            }

            points.push(ll);

            if (points.length > mapConfig.path_max_points) {
                points.shift();
            }

            if (path === null) {
                path = L.polyline(points, { weight: 3 }).addTo(map);
            } else {
                path.setLatLngs(points);
            }

            if (firstFix) {
                map.setView(ll, presentableZoom(p.hacc_m));
                firstFix = false;
            } else {
                map.panTo(ll, { animate: false });
            }
        } catch (e) {
            document.getElementById("status").innerText = "Map update error";
        }
    }

    setInterval(updatePosition, mapConfig.update_interval_ms);
</script>
</body>
</html>
"""


def deep_merge(defaults, user_cfg):
    if not isinstance(user_cfg, dict):
        return defaults

    result = dict(defaults)

    for key, value in user_cfg.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def load_config(path):
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        user_cfg = yaml.safe_load(f) or {}

    return deep_merge(DEFAULT_CONFIG, user_cfg)


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def u1(payload, offset):
    return payload[offset]


def u2(payload, offset):
    return struct.unpack_from("<H", payload, offset)[0]


def u4(payload, offset):
    return struct.unpack_from("<I", payload, offset)[0]


def i4(payload, offset):
    return struct.unpack_from("<i", payload, offset)[0]


def decode_nav_pvt_raw(raw):
    if len(raw) < 8:
        return None

    if raw[0:2] != b"\xb5\x62":
        return None

    msg_class = raw[2]
    msg_id = raw[3]

    if msg_class != 0x01 or msg_id != 0x07:
        return None

    length = int.from_bytes(raw[4:6], "little")
    payload = raw[6:6 + length]

    if len(payload) < 92:
        return None

    fix_type = u1(payload, 20)
    flags = u1(payload, 21)
    num_sv = u1(payload, 23)

    lon_deg = i4(payload, 24) * 1e-7
    lat_deg = i4(payload, 28) * 1e-7

    height_ellipsoid_m = i4(payload, 32) * 1e-3
    alt_msl_m = i4(payload, 36) * 1e-3

    hacc_m = u4(payload, 40) * 1e-3
    vacc_m = u4(payload, 44) * 1e-3

    speed_mps = i4(payload, 60) * 1e-3
    heading_deg = i4(payload, 64) * 1e-5

    speed_acc_mps = u4(payload, 68) * 1e-3
    heading_acc_deg = u4(payload, 72) * 1e-5

    pdop = u2(payload, 76) * 0.01

    gnss_fix_ok = bool(flags & 0b00000001)
    diff_soln = bool(flags & 0b00000010)
    carrier_solution = (flags & 0b11000000) >> 6

    fix_labels = {
        0: "no fix",
        1: "dead reckoning",
        2: "2D",
        3: "3D",
        4: "GNSS + dead reckoning",
        5: "time only",
    }

    carrier_labels = {
        0: "none",
        1: "float",
        2: "fixed",
        3: "reserved",
    }

    return {
        "valid": gnss_fix_ok and fix_type in (2, 3, 4),
        "lat": lat_deg,
        "lon": lon_deg,
        "height_ellipsoid_m": height_ellipsoid_m,
        "alt_msl_m": alt_msl_m,
        "hacc_m": hacc_m,
        "vacc_m": vacc_m,
        "speed_mps": speed_mps,
        "speed_acc_mps": speed_acc_mps,
        "heading_deg": heading_deg,
        "heading_acc_deg": heading_acc_deg,
        "pdop": pdop,
        "fix_type": fix_type,
        "fix_label": fix_labels.get(fix_type, str(fix_type)),
        "gnss_fix_ok": gnss_fix_ok,
        "diff_soln": diff_soln,
        "carrier_solution": carrier_solution,
        "carrier_label": carrier_labels.get(carrier_solution, str(carrier_solution)),
        "num_sv": num_sv,
        "msg": "UBX-NAV-PVT(raw)",
        "timestamp": now_utc_iso(),
    }


def as_float(value):
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def as_int(value):
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def decode_nmea_fallback(parsed):
    lat = as_float(getattr(parsed, "lat", None))
    lon = as_float(getattr(parsed, "lon", None))

    if lat is None or lon is None:
        return None

    alt = as_float(getattr(parsed, "alt", None))
    quality = getattr(parsed, "quality", None)

    num_sv = (
        as_int(getattr(parsed, "numSV", None))
        or as_int(getattr(parsed, "numSVs", None))
        or as_int(getattr(parsed, "numSats", None))
        or as_int(getattr(parsed, "siv", None))
    )

    speed = (
        as_float(getattr(parsed, "spd", None))
        or as_float(getattr(parsed, "sog", None))
    )

    heading = (
        as_float(getattr(parsed, "cog", None))
        or as_float(getattr(parsed, "headMot", None))
    )

    return {
        "valid": True,
        "lat": lat,
        "lon": lon,
        "height_ellipsoid_m": None,
        "alt_msl_m": alt,
        "hacc_m": None,
        "vacc_m": None,
        "speed_mps": speed,
        "speed_acc_mps": None,
        "heading_deg": heading,
        "heading_acc_deg": None,
        "pdop": as_float(getattr(parsed, "PDOP", None)),
        "fix_type": quality,
        "fix_label": str(quality) if quality is not None else None,
        "gnss_fix_ok": True,
        "diff_soln": None,
        "carrier_solution": None,
        "carrier_label": None,
        "num_sv": num_sv,
        "msg": getattr(parsed, "identity", type(parsed).__name__),
        "timestamp": now_utc_iso(),
    }


def extract_position(raw, parsed):
    pos = decode_nav_pvt_raw(raw)

    if pos is not None:
        return pos

    return decode_nmea_fallback(parsed)


def nmea_checksum(sentence_without_dollar):
    checksum = 0

    for ch in sentence_without_dollar:
        checksum ^= ord(ch)

    return checksum


def deg_to_nmea(value, is_lat):
    value = abs(float(value))
    deg = int(value)
    minutes = (value - deg) * 60.0

    if is_lat:
        return f"{deg:02d}{minutes:09.6f}"

    return f"{deg:03d}{minutes:09.6f}"


def build_gga(lat, lon, alt_m, quality, num_sv, hdop):
    now = datetime.now(timezone.utc)
    hhmmss = now.strftime("%H%M%S") + ".00"

    lat_field = deg_to_nmea(lat, is_lat=True)
    lon_field = deg_to_nmea(lon, is_lat=False)

    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"

    body = (
        f"GPGGA,{hhmmss},{lat_field},{ns},{lon_field},{ew},"
        f"{int(quality)},{int(num_sv):02d},{float(hdop):.1f},"
        f"{float(alt_m):.3f},M,0.000,M,,"
    )

    return f"${body}*{nmea_checksum(body):02X}\r\n"


def current_gga():
    with lock:
        p = dict(latest_position)

    if p["valid"] and p["lat"] is not None and p["lon"] is not None:
        carrier = p.get("carrier_solution")

        if carrier == 2:
            quality = 4
        elif carrier == 1:
            quality = 5
        elif p.get("diff_soln"):
            quality = 2
        else:
            quality = 1

        return build_gga(
            lat=p["lat"],
            lon=p["lon"],
            alt_m=p["alt_msl_m"] if p["alt_msl_m"] is not None else 0.0,
            quality=quality,
            num_sv=p["num_sv"] if p["num_sv"] is not None else 0,
            hdop=p["pdop"] if p["pdop"] is not None else 1.0,
        )

    return None


def ntrip_worker(serial_stream):
    ntrip_cfg = CFG["ntrip"]

    host = ntrip_cfg["host"]
    port = int(ntrip_cfg["port"])
    mount = str(ntrip_cfg["mountpoint"]).lstrip("/")
    username = ntrip_cfg["username"]
    password = ntrip_cfg["password"]

    if not host or not mount:
        print("[ntrip] missing host or mountpoint")
        return

    auth_header = ""

    if username or password:
        token = f"{username}:{password}".encode("utf-8")
        auth_header = (
            "Authorization: Basic "
            + base64.b64encode(token).decode("ascii")
            + "\r\n"
        )

    while not stop_event.is_set():
        sock = None

        try:
            raw_sock = socket.create_connection(
                (host, port),
                timeout=float(ntrip_cfg["timeout_s"]),
            )

            if bool(ntrip_cfg["tls"]):
                context = ssl.create_default_context()
                sock = context.wrap_socket(raw_sock, server_hostname=host)
            else:
                sock = raw_sock

            sock.settimeout(1.0)

            request = (
                f"GET /{mount} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Ntrip-Version: Ntrip/2.0\r\n"
                f"User-Agent: NTRIP ublox_live_satmap/1.0\r\n"
                f"Accept: */*\r\n"
                f"Connection: close\r\n"
                f"{auth_header}"
                f"\r\n"
            )

            sock.sendall(request.encode("ascii"))
            print(f"[ntrip] connected to {host}:{port}/{mount}")

            header_data = b""

            while b"\r\n\r\n" not in header_data:
                chunk = sock.recv(4096)

                if not chunk:
                    raise ConnectionError("caster closed before HTTP/NTRIP header")

                header_data += chunk

            header, rest = header_data.split(b"\r\n\r\n", 1)
            header_text = header.decode("latin1", errors="replace")

            if "200 OK" not in header_text and "ICY 200" not in header_text:
                first_line = header_text.splitlines()[0] if header_text else "NTRIP rejected"
                raise ConnectionError(first_line)

            print("[ntrip] stream accepted")

            if rest:
                serial_stream.write(rest)

            last_gga = 0.0
            gga_interval = float(ntrip_cfg["gga_interval_s"])

            while not stop_event.is_set():
                now = time.time()

                if gga_interval > 0 and now - last_gga >= gga_interval:
                    gga = current_gga()

                    if gga is not None:
                        sock.sendall(gga.encode("ascii"))
                        last_gga = now

                try:
                    data = sock.recv(4096)

                    if not data:
                        raise ConnectionError("caster closed stream")

                    serial_stream.write(data)

                except socket.timeout:
                    continue

        except Exception as e:
            reconnect_s = float(ntrip_cfg["reconnect_s"])
            print(f"[ntrip] {e}; reconnecting in {reconnect_s:.1f} s")
            time.sleep(reconnect_s)

        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass


def serial_worker():
    serial_cfg = CFG["serial"]
    ntrip_cfg = CFG["ntrip"]

    port = serial_cfg["port"]
    baud = int(serial_cfg["baud"])
    timeout = float(serial_cfg["timeout"])

    while not stop_event.is_set():
        try:
            with serial.Serial(port, baudrate=baud, timeout=timeout) as stream:
                print(f"[serial] connected to {port} @ {baud}")

                if bool(ntrip_cfg["enabled"]):
                    t_ntrip = threading.Thread(
                        target=ntrip_worker,
                        args=(stream,),
                        daemon=True,
                    )
                    t_ntrip.start()

                reader = UBXReader(stream, protfilter=3, quitonerror=0)

                for raw, parsed in reader:
                    if stop_event.is_set():
                        break

                    if parsed is None:
                        continue

                    pos = extract_position(raw, parsed)

                    if pos is None:
                        continue

                    with lock:
                        latest_position.update(pos)

                    if pos["valid"]:
                        print(
                            f"[fix] {pos['lat']:.8f}, {pos['lon']:.8f} "
                            f"hAcc={pos['hacc_m']} m "
                            f"speed={pos['speed_mps']} m/s "
                            f"heading={pos['heading_deg']} deg "
                            f"RTK={pos['carrier_label']} "
                            f"msg={pos['msg']}"
                        )

        except serial.SerialException as e:
            print(f"[serial] {e}; retrying in 2 s")
            time.sleep(2)

        except KeyboardInterrupt:
            stop_event.set()
            raise

        except Exception as e:
            print(f"[reader] {e}; retrying in 2 s")
            time.sleep(2)


@app.route("/")
def index():
    return render_template_string(
        HTML,
        map_config=CFG["map"],
    )


@app.route("/position")
def position():
    with lock:
        return jsonify(dict(latest_position))


def main():
    global CFG

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration file",
    )
    args = parser.parse_args()

    CFG = load_config(args.config)

    t_serial = threading.Thread(target=serial_worker, daemon=True)
    t_serial.start()

    server_cfg = CFG["server"]
    host = server_cfg["host"]
    port = int(server_cfg["port"])

    print(f"[map] open http://{host}:{port}")

    try:
        app.run(host=host, port=port, debug=False, threaded=True)
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()