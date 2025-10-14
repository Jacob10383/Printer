from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, parse, request

from .logging_utils import get_logger
from .paths import MOONRAKER_ASVC

logger = get_logger("moonraker")


def register_service(name: str, *, asvc_path: Path = MOONRAKER_ASVC) -> bool:
    """Ensure a service name is listed inside moonraker.asvc."""
    asvc_path.parent.mkdir(parents=True, exist_ok=True)
    existing = asvc_path.read_text().splitlines() if asvc_path.exists() else []
    if name in existing:
        logger.info("%s already registered in %s", name, asvc_path)
        return True
    with asvc_path.open("a") as fh:
        if existing and existing[-1].strip():
            fh.write("\n")
        fh.write(f"{name}\n")
    logger.info("Registered %s in %s", name, asvc_path)
    return True


class MoonrakerClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7125, api_key: Optional[str] = None, timeout: int = 3):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.api_key = api_key

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> tuple[int, str]:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        data_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        req = request.Request(url, data=data_bytes, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "ignore")
        except error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "ignore")
        except Exception:
            return 0, ""

    def get(self, path: str) -> tuple[int, str]:
        return self._request("GET", path)

    def post(self, path: str, data: Dict[str, Any]) -> tuple[int, str]:
        return self._request("POST", path, data)

    def delete(self, path: str) -> tuple[int, str]:
        return self._request("DELETE", path)

    # Webcam helpers -----------------------------------------------------
    def list_webcams(self) -> Dict[str, Any]:
        status, body = self.get("/server/webcams/list")
        if status != 200:
            logger.warning("Failed to list Moonraker webcams (status %s)", status)
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def create_webcam(self, name: str, stream_url: str, snapshot_url: str, service: str = "mjpegstreamer") -> bool:
        payload = {
            "name": name,
            "service": service,
            "stream_url": stream_url,
            "snapshot_url": snapshot_url,
        }
        status, _ = self.post("/server/webcams/item", payload)
        return status in (200, 201)

    def update_webcam(self, name: str, stream_url: str, snapshot_url: str, service: str = "mjpegstreamer") -> bool:
        payload = {
            "name": name,
            "service": service,
            "stream_url": stream_url,
            "snapshot_url": snapshot_url,
        }
        encoded = parse.quote(name)
        status, _ = self.post(f"/server/webcams/item?name={encoded}", payload)
        return status in (200, 201)

    def delete_webcam(self, name: str) -> bool:
        encoded = parse.quote(name)
        status, _ = self.delete(f"/server/webcams/item?name={encoded}")
        return status in (200, 204)
