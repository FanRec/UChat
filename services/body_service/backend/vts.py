from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4


class VTSBackendError(RuntimeError):
    pass


class VTSBodyBackend:
    def __init__(
        self,
        *,
        ws_url: str,
        plugin_name: str,
        plugin_developer: str,
        auth_token_path: Path,
        connect_timeout_ms: int,
        request_timeout_ms: int,
        model_hint: str,
    ) -> None:
        self.ws_url = ws_url
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        self.auth_token_path = auth_token_path
        self.connect_timeout_ms = connect_timeout_ms
        self.request_timeout_ms = request_timeout_ms
        self.model_hint = model_hint
        self._lock = threading.RLock()
        self._connection: Any = None
        self._last_probe_at = 0.0
        self._health: dict[str, Any] = {
            "backend_ready": False,
            "backend_type": "vts",
            "ws_connected": False,
            "auth_ready": False,
            "auth_token_present": False,
            "current_model_id": "",
            "current_model_name": "",
            "model_hint": model_hint,
            "hotkey_count": 0,
            "operation_count": 0,
            "parameter_count": 0,
            "last_probe_at": "",
            "last_error": "",
        }
        self._hotkeys_by_name: dict[str, dict[str, Any]] = {}
        self._hotkeys_by_id: dict[str, dict[str, Any]] = {}

    def connect(self) -> None:
        with self._lock:
            self._connect_unlocked()
            self._refresh_model_state_unlocked()

    def close(self) -> None:
        with self._lock:
            conn = self._connection
            self._connection = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def probe(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if now - self._last_probe_at < 1.0:
                return dict(self._health)
            self._last_probe_at = now
            try:
                self._connect_unlocked()
                self._refresh_model_state_unlocked()
                self._health["backend_ready"] = True
                self._health["ws_connected"] = True
                self._health["last_error"] = ""
            except Exception as exc:
                self._health["backend_ready"] = False
                self._health["ws_connected"] = False
                self._health["last_error"] = str(exc)
                self._close_broken_connection_unlocked()
            self._health["last_probe_at"] = _iso_now()
            self._health["auth_token_present"] = self.auth_token_path.exists()
            return dict(self._health)

    def trigger_hotkey(self, hotkey: str) -> dict[str, Any]:
        with self._lock:
            self._connect_unlocked()
            try:
                resolved = self._resolve_hotkey_id_unlocked(hotkey)
                response = self._request_unlocked("HotkeyTriggerRequest", {"hotkeyID": resolved})
                self._health["operation_count"] = int(self._health["operation_count"]) + 1
                return {"hotkey": hotkey, "resolved_hotkey_id": resolved, "response": response.get("data", {})}
            except Exception as exc:
                self._health["last_error"] = str(exc)
                raise

    def apply_tracking(self, values: dict[str, float], *, source: str) -> dict[str, Any]:
        if not values:
            return {"applied": {}, "source": source}
        payload = {
            "faceFound": True,
            "mode": "set",
            "parameterValues": [
                {
                    "id": key,
                    "value": round(float(value), 4),
                    "weight": 1.0,
                }
                for key, value in values.items()
            ],
        }
        with self._lock:
            self._connect_unlocked()
            try:
                response = self._request_unlocked("InjectParameterDataRequest", payload)
                self._health["operation_count"] = int(self._health["operation_count"]) + 1
                self._health["parameter_count"] = int(self._health["parameter_count"]) + len(values)
                return {"applied": dict(values), "source": source, "response": response.get("data", {})}
            except Exception as exc:
                self._health["last_error"] = str(exc)
                raise

    def _connect_unlocked(self) -> None:
        if self._connection is not None:
            return
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise VTSBackendError("websockets package is required for VTube Studio backend") from exc
        self.auth_token_path.parent.mkdir(parents=True, exist_ok=True)
        self._preflight_socket_unlocked()
        conn = connect(self.ws_url, open_timeout=max(self.connect_timeout_ms / 1000, 0.1))
        self._connection = conn
        try:
            self._authenticate_unlocked()
            self._health["auth_ready"] = True
            self._health["ws_connected"] = True
        except Exception:
            self._close_broken_connection_unlocked()
            raise

    def _authenticate_unlocked(self) -> None:
        token = self._load_token()
        if token:
            response = self._request_unlocked(
                "AuthenticationRequest",
                {
                    "pluginName": self.plugin_name,
                    "pluginDeveloper": self.plugin_developer,
                    "authenticationToken": token,
                },
                allow_retry=False,
            )
            if bool(response.get("data", {}).get("authenticated", False)):
                return
        response = self._request_unlocked(
            "AuthenticationTokenRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.plugin_developer,
            },
            allow_retry=False,
        )
        token = str(response.get("data", {}).get("authenticationToken", "")).strip()
        if not token:
            raise VTSBackendError("VTube Studio did not return an authentication token")
        self.auth_token_path.write_text(token, encoding="utf-8")
        auth = self._request_unlocked(
            "AuthenticationRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.plugin_developer,
                "authenticationToken": token,
            },
            allow_retry=False,
        )
        if not bool(auth.get("data", {}).get("authenticated", False)):
            reason = str(auth.get("data", {}).get("reason", "authentication failed"))
            raise VTSBackendError(reason)

    def _refresh_model_state_unlocked(self) -> None:
        current = self._request_unlocked("CurrentModelRequest", {}, allow_retry=False)
        current_data = current.get("data", {})
        model_loaded = bool(current_data.get("modelLoaded", False))
        model_id = str(current_data.get("modelID", ""))
        model_name = str(current_data.get("modelName", ""))
        self._health["current_model_id"] = model_id
        self._health["current_model_name"] = model_name
        if not model_loaded:
            self._hotkeys_by_name.clear()
            self._hotkeys_by_id.clear()
            self._health["hotkey_count"] = 0
            return
        hotkeys = self._request_unlocked("HotkeysInCurrentModelRequest", {"modelID": model_id}, allow_retry=False)
        available = list(hotkeys.get("data", {}).get("availableHotkeys", []))
        self._hotkeys_by_name = {str(item.get("name", "")).strip().lower(): item for item in available if str(item.get("name", "")).strip()}
        self._hotkeys_by_id = {str(item.get("hotkeyID", "")).strip(): item for item in available if str(item.get("hotkeyID", "")).strip()}
        self._health["hotkey_count"] = len(available)

    def _resolve_hotkey_id_unlocked(self, hotkey: str) -> str:
        normalized = hotkey.strip()
        if not normalized:
            raise VTSBackendError("empty hotkey name")
        item = self._hotkeys_by_id.get(normalized)
        if item is not None:
            return normalized
        item = self._hotkeys_by_name.get(normalized.lower())
        if item is None:
            return normalized
        resolved = str(item.get("hotkeyID", "")).strip()
        return resolved or normalized

    def _preflight_socket_unlocked(self) -> None:
        parsed = urlparse(self.ws_url)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            raise VTSBackendError(f"invalid VTS websocket url: {self.ws_url}")
        timeout = max(self.connect_timeout_ms / 1000, 0.1)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return
        except OSError as exc:
            raise VTSBackendError(str(exc)) from exc

    def _request_unlocked(self, message_type: str, data: dict[str, Any], *, allow_retry: bool = True) -> dict[str, Any]:
        if self._connection is None:
            raise VTSBackendError("VTube Studio websocket is not connected")
        payload = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"uchat_{uuid4().hex[:16]}",
            "messageType": message_type,
            "data": data,
        }
        try:
            self._connection.send(json.dumps(payload, ensure_ascii=False))
            raw = self._connection.recv()
        except Exception as exc:
            self._close_broken_connection_unlocked()
            raise VTSBackendError(f"VTube Studio request failed: {exc}") from exc
        response = json.loads(raw)
        if response.get("messageType") == "APIError":
            error_data = response.get("data", {})
            error_id = int(error_data.get("errorID", -1) or -1)
            message = str(error_data.get("message", "unknown api error"))
            if allow_retry and error_id == 50:
                self._close_broken_connection_unlocked()
                self._connect_unlocked()
                return self._request_unlocked(message_type, data, allow_retry=False)
            raise VTSBackendError(f"VTS API error {error_id}: {message}")
        return response

    def _load_token(self) -> str:
        if not self.auth_token_path.exists():
            return ""
        return self.auth_token_path.read_text(encoding="utf-8").strip()

    def _close_broken_connection_unlocked(self) -> None:
        conn = self._connection
        self._connection = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
