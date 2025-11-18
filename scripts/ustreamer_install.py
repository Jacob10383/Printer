#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import shutil
import socket
import re
from pathlib import Path
from urllib import request, parse, error

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import shell  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.moonraker import register_service, MoonrakerClient  # noqa: E402
from lib.file_ops import atomic_copy, ensure_directory, copy_file  # noqa: E402
from lib.paths import BINARIES_DIR, SERVICES_DIR, MOONRAKER_ASVC  # noqa: E402

logger = get_logger("ustreamer")

REPORT_INSTALL_STATUS = ""
REPORT_ERRORS = ""
REPORT_IP = ""
REPORT_CAMERA_STATUS = ""


def log_error(msg: str) -> None:
    global REPORT_ERRORS
    logger.error(msg)
    REPORT_ERRORS += f"{msg}\n"


def run(cmd: str):
    return shell.run(cmd)


def run_ok(cmd: str) -> bool:
    r = run(cmd)
    if r.returncode != 0:
        return False
    return True


def read_init_settings() -> dict:
    settings = {
        'resolution': '',
        'fps': '',
        'port': '',
        'auto_restart_interval': '',
    }
    try:
        with open("/etc/init.d/ustreamer", "r") as f:
            content = f.read()
        # Simple regex for VAR="value" or VAR=value
        m = re.search(r'^RESOLUTION\s*=\s*"?([^"\n]+)"?', content, re.MULTILINE)
        if m:
            settings['resolution'] = m.group(1).strip()
        m = re.search(r'^FPS\s*=\s*"?([^"\n]+)"?', content, re.MULTILINE)
        if m:
            settings['fps'] = m.group(1).strip()
        m = re.search(r'^PORT\s*=\s*"?([^"\n]+)"?', content, re.MULTILINE)
        if m:
            settings['port'] = m.group(1).strip()
        m = re.search(r'^RESTART_INTERVAL\s*=\s*"?([^"\n]+)"?', content, re.MULTILINE)
        if m:
            settings['auto_restart_interval'] = m.group(1).strip()
    except Exception:
        pass
    return settings


def install_ustreamer() -> None:
    global REPORT_INSTALL_STATUS
    logger.info("Installing ustreamer...")

    # Stop existing service/process
    run("/etc/init.d/ustreamer stop 2>/dev/null || true")
    run("killall ustreamer 2>/dev/null || true")

    if run_ok("/etc/init.d/cron enable"):
        logger.info("SUCCESS: Enabled cron service")
    else:
        logger.error("ERROR: Failed to enable cron service")

    binary_src = BINARIES_DIR / "ustreamer_static_arm32"
    binary_dst = Path("/usr/local/bin/ustreamer")

    try:
        # Create directory if it doesn't exist
        binary_dst.parent.mkdir(parents=True, exist_ok=True)


        atomic_copy(binary_src, binary_dst, mode=0o755)

        # Test binary
        if run_ok("/usr/local/bin/ustreamer --help"):
            REPORT_INSTALL_STATUS = "ustreamer installed successfully"
            logger.info("Binary installed and tested successfully")
        else:
            log_error("ustreamer binary test failed")
            REPORT_INSTALL_STATUS = "ustreamer installation failed"

    except Exception as e:
        log_error(f"Failed to install ustreamer binary: {e}")
        REPORT_INSTALL_STATUS = "ustreamer installation failed"


def backup_and_disable_services() -> None:
    logger.info("Disabling conflicting services...")

    # webrtc_local
    webrtc_path = Path("/usr/bin/webrtc_local")
    webrtc_dis = Path("/usr/bin/webrtc_local.disabled")

    if webrtc_path.exists() or webrtc_dis.exists():
        if webrtc_path.exists() and not webrtc_dis.exists():
            try:
                os.replace(str(webrtc_path), str(webrtc_dis))
                logger.info("Disabled webrtc_local")
                run("killall webrtc_local 2>/dev/null || true")
            except Exception:
                pass
        elif webrtc_dis.exists() and not webrtc_path.exists():
            logger.info("webrtc_local already disabled")

    # cam_app
    cam_path = Path("/usr/bin/cam_app")
    cam_orig = Path("/usr/bin/cam_app.orig")

    if cam_path.exists() or cam_orig.exists():
        if cam_path.exists() and not cam_orig.exists():
            try:
                os.replace(str(cam_path), str(cam_orig))
                with open("/usr/bin/cam_app", "w") as f:
                    f.write("#!/bin/sh\nexit 0\n")
                os.chmod("/usr/bin/cam_app", 0o755)
                logger.info("Replaced cam_app with dummy script")
            except Exception:
                pass
        elif cam_orig.exists() and cam_path.exists():
            try:
                with open(str(cam_path), "rb") as f:
                    content = f.read(32)
                if b"exit 0" in content:
                    logger.info("cam_app already replaced with dummy")
            except Exception:
                pass

    # Disable old mjpg_streamer if present
    if Path("/etc/init.d/mjpg_streamer").exists():
        try:
            run("/etc/init.d/mjpg_streamer stop")
            run("/etc/init.d/mjpg_streamer disable")
            logger.info("Disabled old mjpg_streamer service")
        except Exception:
            pass


def create_ustreamer_service() -> None:
    logger.info("Creating ustreamer service...")
    src = SERVICES_DIR / "ustreamer"
    try:
        copy_file(src, "/etc/init.d/ustreamer", mode=0o755)
        logger.info("Created ustreamer init script")
    except Exception as exc:
        log_error(f"Failed to create ustreamer init script: {exc}")


def configure_services() -> None:
    logger.info("Configuring services...")

    try:
        register_service("ustreamer", asvc_path=MOONRAKER_ASVC)
    except Exception as exc:
        log_error(f"Failed to register with Moonraker: {exc}")

    if run_ok("/etc/init.d/ustreamer restart"):
        logger.info("Started ustreamer service")
    else:
        log_error("Failed to start ustreamer service")

    if run_ok("/etc/init.d/ustreamer enable"):
        logger.info("Enabled ustreamer service")
    else:
        log_error("Failed to enable ustreamer service")


def get_ip_address() -> str:

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    return "127.0.0.1"


def http_get(url: str) -> tuple[int, str]:
    try:
        with request.urlopen(url, timeout=3) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "ignore")
    except Exception:
        return 0, ""


def http_post(url: str, data: dict) -> int:
    try:
        data_bytes = json.dumps(data).encode("utf-8")
        req = request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=3) as resp:
            return resp.getcode()
    except error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def http_delete(url: str) -> int:
    try:
        req = request.Request(url, method="DELETE")
        with request.urlopen(req, timeout=3) as resp:
            return resp.getcode()
    except error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def get_existing_cameras(ip_address: str) -> str:
    code, body = http_get(f"http://{ip_address}:7125/server/webcams/list")
    if code and body and '"webcams"' in body:
        return body
    return ""


def check_camera_exists(json_response: str, target_ip: str) -> bool:
    return f"{target_ip}:8080" in json_response


def extract_camera_name(json_response: str) -> str:
    import re
    m = re.search(r'"name"\s*:\s*"([^"]*)"', json_response)
    return m.group(1) if m else ""


def check_camera_configured_correctly(json_response: str, target_ip: str) -> bool:
    good_stream = f'"stream_url"\s*:\s*"http://{target_ip}:8080/stream"' in json_response
    good_snap = f'"snapshot_url"\s*:\s*"http://{target_ip}:8080/snapshot"' in json_response
    good_service = '"service"\s*:\s*"uv4l-mjpeg"' in json_response
    return good_stream and good_snap and good_service


 


def delete_camera(ip_address: str, camera_name: str) -> bool:
    encoded_name = parse.quote(camera_name)
    code = http_delete(f"http://{ip_address}:7125/server/webcams/item?name={encoded_name}")
    if code in (200, 204):
        logger.info("SUCCESS: Deleted camera: %s", camera_name)
        return True
    return False


def update_camera(ip_address: str, camera_name: str) -> bool:
    encoded_name = parse.quote(camera_name)
    payload = {
        "name": camera_name,
        "service": "uv4l-mjpeg",
        "stream_url": f"http://{ip_address}:8080/stream",
        "snapshot_url": f"http://{ip_address}:8080/snapshot",
    }
    code = http_post(f"http://{ip_address}:7125/server/webcams/item?name={encoded_name}", payload)
    if code in (200, 201):
        logger.info("SUCCESS: Updated camera: %s", camera_name)
        return True
    return False


def create_camera(ip_address: str) -> bool:
    payload = {
        "name": "Front",
        "service": "uv4l-mjpeg",
        "stream_url": f"http://{ip_address}:8080/stream",
        "snapshot_url": f"http://{ip_address}:8080/snapshot",
    }
    code = http_post(f"http://{ip_address}:7125/server/webcams/item", payload)
    if code in (200, 201):
        logger.info("SUCCESS: Camera created successfully")
        global REPORT_CAMERA_STATUS
        REPORT_CAMERA_STATUS = "configured"
        return True
    else:
        log_error(f"Failed to create camera (HTTP {code})")
        REPORT_CAMERA_STATUS = "error"
        return False


def manage_camera(ip_address: str) -> None:
    logger.info("INFO: Configuring Moonraker webcam...")
    logger.info("INFO: Checking for existing cameras...")

    resp = get_existing_cameras(ip_address)
    if not resp:
        logger.info("INFO: No response from server; creating new camera...")
        create_camera(ip_address)
        return

    if check_camera_exists(resp, ip_address):
        name = extract_camera_name(resp) or "Front"
        logger.info("INFO: Found camera: %s", name)
        if check_camera_configured_correctly(resp, ip_address):
            logger.info("INFO: Configuration is correct; no changes needed")
            global REPORT_CAMERA_STATUS
            REPORT_CAMERA_STATUS = "already_configured"
        else:
            logger.info("INFO: Configuration needs updating...")
            if update_camera(ip_address, name):
                REPORT_CAMERA_STATUS = "updated"
            else:
                if delete_camera(ip_address, name):
                    create_camera(ip_address)
    else:
        logger.info("INFO: No camera found for this IP; creating new one...")
        create_camera(ip_address)


def restart_moonraker() -> None:
    logger.info("INFO: Restarting Moonraker service...")
    if run_ok("/etc/init.d/moonraker restart"):
        logger.info("SUCCESS: Moonraker service restarted successfully")
    else:
        logger.error("ERROR: Failed to restart Moonraker")


def print_final_report() -> None:
    logger.info("")
    logger.info("=== USTREAMER INSTALLATION COMPLETE ===")
    logger.info("")

    if not REPORT_ERRORS:
        logger.info("Installation Status: %s", REPORT_INSTALL_STATUS)

        result = run("pidof ustreamer")
        if result.returncode == 0 and result.stdout.strip():
            logger.info("Service Status: Running")
        else:
            logger.info("Service Status: Not running")

        if REPORT_CAMERA_STATUS == "already_configured":
            logger.info("Camera: Already configured")
        elif REPORT_CAMERA_STATUS in ("configured", "updated"):
            logger.info("Camera: Configured successfully")
        else:
            logger.info("Camera: Configuration failed")

        settings = read_init_settings()
        logger.info("")
        logger.info("Configuration:")
        res = settings.get('resolution', '')
        fps = settings.get('fps', '')
        port = settings.get('port', '')
        ari = settings.get('auto_restart_interval', '')
        logger.info("  - Resolution: %s @ %s fps", res, fps)
        logger.info("  - Port: %s", port)
        logger.info("  - Auto-restart: Every %s minutes", ari)

        logger.info("")
        logger.info("Access URLs:")
        logger.info("  - Stream: http://%s:%s/stream", REPORT_IP, port)
        logger.info("  - Snapshot: http://%s:%s/snapshot", REPORT_IP, port)

    else:
        logger.error("ERRORS ENCOUNTERED")
        logger.info("")
        if REPORT_INSTALL_STATUS:
            logger.info("Installation: %s", REPORT_INSTALL_STATUS)
        logger.info("")
        logger.info("Errors:")
        for line in REPORT_ERRORS.strip().splitlines():
            logger.info("  - %s", line)
        logger.info("")
        logger.info("Despite errors, attempting to show current status:")
        result = run("pidof ustreamer")
        if result.returncode == 0 and result.stdout.strip():
            logger.info("  OK: ustreamer is running")
        else:
            logger.error("  ERROR: ustreamer is not running")

    logger.info("")
    logger.info("Commands:")
    logger.info("  - Logs: tail -f /var/log/ustreamer.log")
    logger.info("  - Restart: /etc/init.d/ustreamer restart")
    logger.info("")


def main() -> None:
    logger.info("=== USTREAMER INSTALLATION ===")
    logger.info("")

    global REPORT_IP

    REPORT_IP = get_ip_address()
    logger.info("INFO: System IP: %s", REPORT_IP)
    logger.info("")

    # Installation steps
    install_ustreamer()
    backup_and_disable_services()
    create_ustreamer_service()
    configure_services()
    manage_camera(REPORT_IP)
    restart_moonraker()
    print_final_report()


if __name__ == "__main__":
    if os.geteuid() != 0:
        logger.error("This script must be run as root")
        sys.exit(1)
    main()
    # Propagate failure to caller if any errors were recorded during installation
    if REPORT_ERRORS:
        sys.exit(1)
    sys.exit(0)
 
