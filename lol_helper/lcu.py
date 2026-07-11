from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LCUError(RuntimeError):
    pass


class LCUNotRunning(LCUError):
    pass


class LCUPermissionDenied(LCUNotRunning):
    pass


class LCUResponseError(LCUError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"LCU 返回 HTTP {status}: {body}")
        self.status = status
        self.body = body


@dataclass(frozen=True, slots=True)
class Credentials:
    port: int
    token: str


def _extract_credentials(command_line: str) -> Credentials | None:
    port = re.search(r"--app-port(?:=|\s+)(\d+)", command_line)
    token = re.search(r'--remoting-auth-token(?:=|\s+)"?([^\s"]+)"?', command_line)
    if not port or not token:
        return None
    return Credentials(int(port.group(1)), token.group(1))


def _credentials_from_lockfile(path: Path) -> Credentials | None:
    try:
        parts = path.read_text(encoding="utf-8").strip().split(":")
        if len(parts) >= 5 and parts[2].isdigit() and parts[3]:
            return Credentials(int(parts[2]), parts[3])
    except (OSError, UnicodeError):
        pass
    return None


def _lockfile_candidates() -> list[Path]:
    candidates: list[Path] = []
    for drive in "CDEFG":
        for relative in (
            "Riot Games/League of Legends/lockfile",
            "WeGameApps/英雄联盟/LeagueClient/lockfile",
            "Program Files/Riot Games/League of Legends/lockfile",
        ):
            candidates.append(Path(f"{drive}:/{relative}"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Riot Games" / "League of Legends" / "lockfile")
    return candidates


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def restart_as_admin() -> bool:
    if is_elevated():
        return False
    if getattr(sys, "frozen", False):
        executable = sys.executable
        parameters = subprocess.list2cmdline(sys.argv[1:])
        working_directory = str(Path(sys.executable).resolve().parent)
    else:
        executable = sys.executable
        script = str(Path(sys.argv[0]).resolve())
        parameters = subprocess.list2cmdline([script, *sys.argv[1:]])
        working_directory = str(Path.cwd())
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, parameters, working_directory, 1
    )
    return result > 32


def discover_credentials() -> Credentials:
    script = (
        "$p=Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\" | "
        "Select-Object -First 1 -ExpandProperty CommandLine; if($p){$p}"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LCUNotRunning("无法查询英雄联盟客户端进程") from exc
    credentials = _extract_credentials(result.stdout)
    if credentials:
        return credentials
    for path in _lockfile_candidates():
        credentials = _credentials_from_lockfile(path)
        if credentials:
            return credentials
    try:
        probe = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "if(Get-Process LeagueClientUx -ErrorAction SilentlyContinue){'running'}"],
            capture_output=True, text=True, timeout=4, creationflags=flags, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        probe = None
    if probe and "running" in probe.stdout:
        raise LCUPermissionDenied("检测到客户端，但无权读取 LCU 凭据；请以管理员身份重启助手")
    raise LCUNotRunning("未发现已启动的 LeagueClientUx.exe")


class LCUClient:
    def __init__(self, credentials: Credentials | None = None, timeout: float = 2.0):
        self.credentials = credentials or discover_credentials()
        self.timeout = timeout
        self._ssl_context = ssl._create_unverified_context()

    def request(self, method: str, path: str, data: Any = None) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        raw = None if data is None else json.dumps(data).encode("utf-8")
        auth = base64.b64encode(f"riot:{self.credentials.token}".encode()).decode()
        request = urllib.request.Request(
            f"https://127.0.0.1:{self.credentials.port}{path}",
            data=raw,
            method=method.upper(),
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(body)
            except ValueError:
                pass
            raise LCUResponseError(exc.code, body) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LCUNotRunning("LCU 连接已断开") from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, data: Any = None) -> Any:
        return self.request("POST", path, data)

    def patch(self, path: str, data: Any) -> Any:
        return self.request("PATCH", path, data)
