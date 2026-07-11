from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ROOT, Settings
from .lcu import LCUClient, LCUNotRunning, LCUPermissionDenied, LCUResponseError


EventCallback = Callable[[str, Any], None]


def _int(value: Any) -> int | None:
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def find_local_cell_id(session: dict[str, Any]) -> int | None:
    value = session.get("localPlayerCellId")
    return int(value) if isinstance(value, int) else None


def current_pick_action(session: dict[str, Any]) -> dict[str, Any] | None:
    cell_id = find_local_cell_id(session)
    for group in session.get("actions", []):
        for action in group if isinstance(group, list) else []:
            if (
                action.get("actorCellId") == cell_id
                and action.get("type") == "pick"
                and action.get("isInProgress")
                and not action.get("completed")
            ):
                return action
    return None


def bench_ids(session: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    for item in session.get("benchChampions", []) or []:
        if isinstance(item, dict):
            champion_id = _int(item.get("championId"))
        else:
            champion_id = _int(item)
        if champion_id:
            result.add(champion_id)
    return result


def card_ids(session: dict[str, Any]) -> set[int]:
    """Best-effort extraction across classic and Champion Cards session shapes."""
    keys = {"championIds", "championCardIds", "availableChampionIds", "pickableChampionIds"}
    found: set[int] = set()

    def walk(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in keys and isinstance(child, list):
                    found.update(item for item in (_int(v) for v in child) if item)
                elif key in {"championId", "id"} and "card" in parent_key.lower():
                    champion_id = _int(child)
                    if champion_id:
                        found.add(champion_id)
                walk(child, key)
        elif isinstance(value, list):
            for child in value:
                walk(child, parent_key)

    walk(session)
    return found


def pickable_ids(session: dict[str, Any]) -> set[int]:
    """All directly displayed choices, retained as a compatibility helper."""
    return card_ids(session) | bench_ids(session)


def current_champion_id(session: dict[str, Any]) -> int | None:
    cell_id = find_local_cell_id(session)
    for player in session.get("myTeam", []) or []:
        if isinstance(player, dict) and player.get("cellId") == cell_id:
            return _int(player.get("championId") or player.get("championPickIntent"))
    return None


def redact_session(value: Any) -> Any:
    private = {"puuid", "summonerId", "displayName", "gameName", "tagLine", "name"}
    if isinstance(value, dict):
        return {key: ("<redacted>" if key in private else redact_session(child)) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_session(child) for child in value]
    return value


class AutomationEngine:
    def __init__(self, settings_provider: Callable[[], Settings], event: EventCallback, logger: logging.Logger):
        self._settings_provider = settings_provider
        self._event = event
        self._logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: LCUClient | None = None
        self._accepted_id: str | None = None
        self._picked_session = False
        self._swap_attempt: dict[int, float] = {}
        self._last_session: dict[str, Any] | None = None
        self._catalog_loaded = False
        self._connection_message = "等待英雄联盟客户端启动"
        self._target_lock = threading.Lock()
        self._target_champion_id: int | None = None

    def set_target_champion(self, champion_id: int | None) -> None:
        with self._target_lock:
            if champion_id == self._target_champion_id:
                return
            self._target_champion_id = champion_id
            self._picked_session = False
            self._swap_attempt.clear()
        if champion_id:
            self._emit("info", f"本局目标已更换为英雄 ID {champion_id}")
        else:
            self._event("info", "已清除本局选取目标")

    def _target(self) -> int | None:
        with self._target_lock:
            return self._target_champion_id

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lcu-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def save_diagnostic(self) -> Path:
        if self._last_session is None:
            raise RuntimeError("当前没有可保存的选人会话")
        folder = ROOT / "diagnostics"
        folder.mkdir(exist_ok=True)
        path = folder / f"champ-select-{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps(redact_session(self._last_session), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _emit(self, level: str, message: str) -> None:
        getattr(self._logger, level if level in {"info", "warning", "error"} else "info")(message)
        self._event(level, message)

    def _connect(self) -> bool:
        try:
            self._client = LCUClient()
            self._client.get("/lol-gameflow/v1/gameflow-phase")
            self._emit("info", "已连接英雄联盟客户端")
            self._load_champion_catalog()
            return True
        except LCUPermissionDenied as exc:
            self._client = None
            self._connection_message = str(exc)
            return False
        except LCUNotRunning:
            self._client = None
            self._connection_message = "等待英雄联盟客户端启动"
            return False

    def _load_champion_catalog(self) -> None:
        if self._catalog_loaded or self._client is None:
            return
        try:
            raw = self._client.get("/lol-game-data/assets/v1/champion-summary.json")
        except LCUResponseError:
            return
        if not isinstance(raw, list):
            return
        catalog: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            champion_id = _int(item.get("id"))
            name = item.get("name")
            if champion_id and isinstance(name, str) and name:
                catalog.append({"id": champion_id, "name": name})
        if catalog:
            catalog.sort(key=lambda item: item["name"])
            self._catalog_loaded = True
            self._event("champions", catalog)

    def _run(self) -> None:
        last_connection_message: str | None = None
        while not self._stop.is_set():
            if self._client is None and not self._connect():
                if self._connection_message != last_connection_message:
                    level = "permission" if "管理员" in self._connection_message else "offline"
                    self._event(level, self._connection_message)
                    last_connection_message = self._connection_message
                self._stop.wait(2.0)
                continue
            last_connection_message = None
            try:
                self._tick()
            except LCUNotRunning:
                self._client = None
                self._last_session = None
                self._catalog_loaded = False
                self._event("offline", "客户端已断开，正在等待重连")
            except Exception as exc:  # keep the monitor alive and retain diagnostics
                self._logger.exception("监控循环异常")
                self._event("error", f"监控异常：{exc}")
            interval = max(150, min(self._settings_provider().poll_interval_ms, 2000)) / 1000
            self._stop.wait(interval)

    def _tick(self) -> None:
        assert self._client is not None
        settings = self._settings_provider()
        phase = self._client.get("/lol-gameflow/v1/gameflow-phase")
        self._event("status", f"客户端在线 · {phase}")
        if settings.auto_accept and phase == "ReadyCheck":
            self._accept()
        if phase == "ChampSelect":
            self._champ_select(settings)
        else:
            self._last_session = None
            self._picked_session = False
            self._swap_attempt.clear()
            if self._target() is not None:
                with self._target_lock:
                    self._target_champion_id = None
                self._event("target_cleared", "已离开选角，本局目标已清空")

    def _accept(self) -> None:
        assert self._client is not None
        try:
            check = self._client.get("/lol-matchmaking/v1/ready-check") or {}
            check_id = str(check.get("timer", check.get("state", "ready")))
            if check.get("state") == "InProgress" and check_id != self._accepted_id:
                self._client.post("/lol-matchmaking/v1/ready-check/accept")
                self._accepted_id = check_id
                self._emit("info", "已自动接受对局")
        except LCUResponseError as exc:
            if exc.status not in {404, 409}:
                raise

    def _champ_select(self, settings: Settings) -> None:
        assert self._client is not None
        session = self._client.get("/lol-champ-select/v1/session")
        if not isinstance(session, dict):
            return
        self._last_session = session
        cards = card_ids(session)
        bench = bench_ids(session)
        current = current_champion_id(session)
        target = self._target()
        self._event("champ_select", {
            "cards": sorted(cards),
            "bench": sorted(bench),
            "current": current,
            "target": target,
        })
        if target is None or target == current:
            return
        if settings.auto_bench_swap and target in bench:
            self._try_bench_swap(target)
        elif settings.auto_pick and target in cards and not self._picked_session:
            self._try_pick(session, target)

    def _try_pick(self, session: dict[str, Any], chosen: int) -> None:
        assert self._client is not None
        action = current_pick_action(session)
        if not action:
            return
        action_id = action.get("id")
        try:
            self._client.patch(
                f"/lol-champ-select/v1/session/actions/{action_id}",
                {"championId": chosen, "completed": True},
            )
            self._picked_session = True
            self._emit("info", f"已按优先级选择英雄 ID {chosen}")
        except LCUResponseError as exc:
            # The server remains authoritative; don't spam rejected card picks.
            if exc.status in {400, 404, 409}:
                self._picked_session = True
                self._emit("warning", f"客户端拒绝自动选择英雄 ID {chosen}，已停止本局重试")
            else:
                raise

    def _try_bench_swap(self, champion_id: int) -> None:
        assert self._client is not None
        now = time.monotonic()
        if now < self._swap_attempt.get(champion_id, 0):
            return
        try:
            self._client.post(f"/lol-champ-select/v1/session/bench/swap/{champion_id}")
            self._swap_attempt[champion_id] = now + 30
            self._emit("info", f"已交换到公共池英雄 ID {champion_id}")
        except LCUResponseError as exc:
            if exc.status in {400, 404, 409}:
                # The target remains selected and is retried as soon as the
                # server-side per-champion cooldown permits it.
                self._swap_attempt[champion_id] = now + 0.25
                return
            raise
