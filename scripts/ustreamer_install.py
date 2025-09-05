#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import shutil
import socket
import subprocess
import re
from pathlib import Path
from urllib import request, parse, error
REPORT_INSTALL_STATUS=""
REPORT_ERRORS=""
REPORT_IP=""
REPORT_CAMERA_STATUS=""


def log_error(msg: str) -> None:
    global REPORT_ERRORS
    sys.stderr.write(f"\033[0;31mERROR: {msg}\033[0m\n")
    REPORT_ERRORS += f"{msg}\n"

def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, text=True, capture_output=True)


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
    print("INFO: Installing ustreamer...")

    # Stop existing service/process
    run("/etc/init.d/ustreamer stop 2>/dev/null || true")
    run("killall ustreamer 2>/dev/null || true")

    if run_ok("/etc/init.d/cron enable"):
        print("SUCCESS: Enabled cron service")
    else:
        print("ERROR: Failed to enable cron service")

    repo_root = Path(__file__).resolve().parent.parent
    binary_src = repo_root / "binaries" / "ustreamer_static_arm32"
    binary_dst = Path("/usr/local/bin/ustreamer")

    try:
        # Create directory if it doesn't exist
        binary_dst.parent.mkdir(parents=True, exist_ok=True)


        tmp_path = binary_dst.parent / "ustreamer.new"
        shutil.copy2(binary_src, tmp_path)
        os.chmod(tmp_path, 0o755)
        os.replace(tmp_path, binary_dst)

        # Test binary
        if run_ok("/usr/local/bin/ustreamer --help"):
            REPORT_INSTALL_STATUS = "ustreamer installed successfully"
            print("SUCCESS: Binary installed and tested successfully")
        else:
            log_error("ustreamer binary test failed")
            REPORT_INSTALL_STATUS = "ustreamer installation failed"

    except Exception as e:
        log_error(f"Failed to install ustreamer binary: {e}")
        REPORT_INSTALL_STATUS = "ustreamer installation failed"


def backup_and_disable_services() -> None:
    print("INFO: Disabling conflicting services...")

    # webrtc_local
    webrtc_path = Path("/usr/bin/webrtc_local")
    webrtc_dis = Path("/usr/bin/webrtc_local.disabled")

    if webrtc_path.exists() or webrtc_dis.exists():
        if webrtc_path.exists() and not webrtc_dis.exists():
            try:
                os.replace(str(webrtc_path), str(webrtc_dis))
                print("SUCCESS: Disabled webrtc_local")
                run("killall webrtc_local 2>/dev/null || true")
            except Exception:
                pass
        elif webrtc_dis.exists() and not webrtc_path.exists():
            print("INFO: webrtc_local already disabled")

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
                print("SUCCESS: Replaced cam_app with dummy script")
            except Exception:
                pass
        elif cam_orig.exists() and cam_path.exists():
            try:
                with open(str(cam_path), "rb") as f:
                    content = f.read(32)
                if b"exit 0" in content:
                    print("INFO: cam_app already replaced with dummy")
            except Exception:
                pass

    # Disable old mjpg_streamer if present
    if Path("/etc/init.d/mjpg_streamer").exists():
        try:
            run("/etc/init.d/mjpg_streamer stop")
            run("/etc/init.d/mjpg_streamer disable")
            print("SUCCESS: Disabled old mjpg_streamer service")
        except Exception:
            pass


def create_ustreamer_service() -> None:
    print("INFO: Creating ustreamer service...")
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "services" / "ustreamer"
    try:
        shutil.copyfile(src, "/etc/init.d/ustreamer")
        os.chmod("/etc/init.d/ustreamer", 0o755)
        print("SUCCESS: Created ustreamer init script")
    except Exception:
        print("ERROR: Failed to create ustreamer init script")


def configure_services() -> None:
    print("INFO: Configuring services...")

    try:
        asvc_path = "/mnt/UDISK/printer_data/moonraker.asvc"
        existing = ""
        if Path(asvc_path).exists():
            with open(asvc_path, "r") as f:
                existing = f.read()
        if "\nustreamer\n" in f"\n{existing}\n":
            print("INFO: ustreamer already registered with Moonraker")
        else:
            with open(asvc_path, "a") as f:
                if not existing.endswith('\n'):
                    f.write('\n')
                f.write("ustreamer\n")
            print("SUCCESS: Registered ustreamer with Moonraker")
    except Exception:
        print("ERROR: Failed to register with Moonraker")

    if run_ok("/etc/init.d/ustreamer restart"):
        print("SUCCESS: Started ustreamer service")
    else:
        print("ERROR: Failed to start ustreamer service")

    if run_ok("/etc/init.d/ustreamer enable"):
        print("SUCCESS: Enabled ustreamer service")
    else:
        print("ERROR: Failed to enable ustreamer service")


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
    good_service = '"service"\s*:\s*"mjpegstreamer"' in json_response
    return good_stream and good_snap and good_service


 


def delete_camera(ip_address: str, camera_name: str) -> bool:
    encoded_name = parse.quote(camera_name)
    code = http_delete(f"http://{ip_address}:7125/server/webcams/item?name={encoded_name}")
    if code in (200, 204):
        print(f"SUCCESS: Deleted camera: {camera_name}")
        return True
    return False


def update_camera(ip_address: str, camera_name: str) -> bool:
    encoded_name = parse.quote(camera_name)
    payload = {
        "name": camera_name,
        "service": "mjpegstreamer",
        "stream_url": f"http://{ip_address}:8080/stream",
        "snapshot_url": f"http://{ip_address}:8080/snapshot",
    }
    code = http_post(f"http://{ip_address}:7125/server/webcams/item?name={encoded_name}", payload)
    if code in (200, 201):
        print(f"SUCCESS: Updated camera: {camera_name}")
        return True
    return False


def create_camera(ip_address: str) -> bool:
    payload = {
        "name": "Front",
        "service": "mjpegstreamer",
        "stream_url": f"http://{ip_address}:8080/stream",
        "snapshot_url": f"http://{ip_address}:8080/snapshot",
    }
    code = http_post(f"http://{ip_address}:7125/server/webcams/item", payload)
    if code in (200, 201):
        print("SUCCESS: Camera created successfully")
        global REPORT_CAMERA_STATUS
        REPORT_CAMERA_STATUS = "configured"
        return True
    else:
        log_error(f"Failed to create camera (HTTP {code})")
        REPORT_CAMERA_STATUS = "error"
        return False


def manage_camera(ip_address: str) -> None:
    print("INFO: Configuring Moonraker webcam...")
    print("INFO: Checking for existing cameras...")

    resp = get_existing_cameras(ip_address)
    if not resp:
        print("INFO: No response from server; creating new camera...")
        create_camera(ip_address)
        return

    if check_camera_exists(resp, ip_address):
        name = extract_camera_name(resp) or "Front"
        print(f"INFO: Found camera: {name}")
        if check_camera_configured_correctly(resp, ip_address):
            print("INFO: Configuration is correct; no changes needed")
            global REPORT_CAMERA_STATUS
            REPORT_CAMERA_STATUS = "already_configured"
        else:
            print("INFO: Configuration needs updating...")
            if update_camera(ip_address, name):
                REPORT_CAMERA_STATUS = "updated"
            else:
                if delete_camera(ip_address, name):
                    create_camera(ip_address)
    else:
        print("INFO: No camera found for this IP; creating new one...")
        create_camera(ip_address)


def restart_moonraker() -> None:
    print("INFO: Restarting Moonraker service...")
    if run_ok("/etc/init.d/moonraker restart"):
        print("SUCCESS: Moonraker service restarted successfully")
    else:
        print("ERROR: Failed to restart Moonraker")


def print_final_report() -> None:
    print("")
    print("=== USTREAMER INSTALLATION COMPLETE ===")
    print("")

    if not REPORT_ERRORS:
        print(f"Installation Status: {REPORT_INSTALL_STATUS}")

        # Service status
        result = run("pidof ustreamer")
        if result.returncode == 0 and result.stdout.strip():
            print("Service Status: Running")
        else:
            print("Service Status: Not running")

        # Camera status
        if REPORT_CAMERA_STATUS == "already_configured":
            print("Camera: Already configured")
        elif REPORT_CAMERA_STATUS in ("configured", "updated"):
            print("Camera: Configured successfully")
        else:
            print("Camera: Configuration failed")

        settings = read_init_settings()
        print("")
        print("Configuration:")
        res = settings.get('resolution', '')
        fps = settings.get('fps', '')
        port = settings.get('port', '')
        ari = settings.get('auto_restart_interval', '')
        print(f"  - Resolution: {res} @ {fps} fps")
        print(f"  - Port: {port}")
        print(f"  - Auto-restart: Every {ari} minutes")

        print("")
        print("Access URLs:")
        print(f"  - Stream: http://{REPORT_IP}:{port}/stream")
        print(f"  - Snapshot: http://{REPORT_IP}:{port}/snapshot")

    else:
        print("ERRORS ENCOUNTERED")
        print("")
        if REPORT_INSTALL_STATUS:
            print(f"Installation: {REPORT_INSTALL_STATUS}")
        print("")
        print("Errors:")
        for line in REPORT_ERRORS.strip().splitlines():
            print(f"  - {line}")
        print("")
        print("Despite errors, attempting to show current status:")
        result = run("pidof ustreamer")
        if result.returncode == 0 and result.stdout.strip():
            print("  OK: ustreamer is running")
        else:
            print("  ERROR: ustreamer is not running")

    print("")
    print("Commands:")
    print("  - Logs: tail -f /var/log/ustreamer.log")
    print("  - Restart: /etc/init.d/ustreamer restart")
    print("")


def main() -> None:
    print("=== USTREAMER INSTALLATION ===")
    print("")

    global REPORT_IP

    REPORT_IP = get_ip_address()
    print(f"INFO: System IP: {REPORT_IP}")
    print("")

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
        print("ERROR: This script must be run as root", file=sys.stderr)
        sys.exit(1)
    main()
    # Propagate failure to caller if any errors were recorded during installation
    if REPORT_ERRORS:
        sys.exit(1)
    sys.exit(0)
 