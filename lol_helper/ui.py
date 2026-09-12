from __future__ import annotations

import concurrent.futures
import queue
import tkinter as tk
import time
from collections.abc import Collection
from dataclasses import replace
from typing import Any

from .automation import AutomationEngine
from .config import Settings
from .ddragon import ChampionSummary, DataDragon
from .logging_setup import configure_logging
from .window_docking import (
    enable_native_window_transitions,
    league_client_window,
    sync_bar_z_order,
    system_window_animations_enabled,
    show_in_taskbar,
    top_bar_geometry,
)


BG = "#07131f"
CARD = "#102638"
ACTIVE_CARD = "#17394f"
FRAME_BORDER = "#5b4930"
IDLE_BORDER = "#294657"
GOLD = "#c99b3d"

BAR_HEIGHT = 72
DOCK_INTERVAL_MS = 350
TRANSIENT_STATE_GRACE_SECONDS = 0.8

# League's ARAM bench is laid out on a 1280-wide design canvas. These values
# were measured from the client at 1280x720 and scale with its outer width.
DESIGN_CLIENT_WIDTH = 1280
DESIGN_FIRST_SLOT_CENTER = 377.0
DESIGN_SLOT_PITCH = 58.4
DESIGN_SLOT_SIZE = 50.0
MIN_SLOT_SIZE = 40
MAX_SLOT_SIZE = 62


def bench_slot_layout(
    client_width: int,
    champion_count: int,
) -> list[tuple[int, int]]:
    """Return left/size pairs aligned with League's fixed ARAM bench slots."""
    scale = max(0.1, client_width / DESIGN_CLIENT_WIDTH)
    slot_size = max(
        MIN_SLOT_SIZE,
        min(MAX_SLOT_SIZE, round(DESIGN_SLOT_SIZE * scale)),
    )
    result: list[tuple[int, int]] = []
    for index in range(max(0, champion_count)):
        center = round(
            (DESIGN_FIRST_SLOT_CENTER + DESIGN_SLOT_PITCH * index) * scale
        )
        result.append((center - slot_size // 2, slot_size))
    return result


def should_show_bar(
    phase: str | None,
    bench: Collection[int],
    client_rect: tuple[int, int, int, int] | None,
    available_portraits: Collection[int],
) -> bool:
    """Keep the overlay absent unless every visible portrait is ready."""
    return (
        phase == "ChampSelect"
        and bool(bench)
        and client_rect is not None
        and all(champion_id in available_portraits for champion_id in bench)
    )


class App:
    def __init__(self, root: tk.Tk):
        from .tray import TrayIcon

        self.root = root
        self.root.title("LOL Helper")
        self.root.configure(bg=FRAME_BORDER)
        self.root.geometry(f"600x{BAR_HEIGHT}+0+0")
        self.root.attributes("-topmost", False)
        self.root.overrideredirect(True)
        self._app_icon = self._create_app_icon()
        self.root.iconphoto(True, self._app_icon)

        self.settings = Settings.load()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="assets"
        )
        self.ddragon = DataDragon()
        self.summaries: dict[int, ChampionSummary] = {}
        self.portrait_paths: dict[int, str] = {}
        self.photos: dict[int, tk.PhotoImage] = {}
        self.asset_batch: tuple[int, ...] = ()
        self.preload_started = False
        self.session_state: dict[str, Any] | None = None
        self.current_phase: str | None = None
        self.client_rect: tuple[int, int, int, int] | None = None
        self.client_missing_since: float | None = None
        self.bench_empty_since: float | None = None
        self.last_render_signature: tuple[Any, ...] | None = None
        self.bar_visible = False
        self.client_hwnd: int | None = None
        self.cards: dict[int, tk.Frame] = {}
        self.animation_jobs: dict[int, str] = {}
        self.animations_enabled = system_window_animations_enabled()

        self.engine = AutomationEngine(
            self._settings_snapshot, self._on_event, configure_logging()
        )

        self._build()
        self.root.update_idletasks()
        show_in_taskbar(self.root.winfo_id())
        enable_native_window_transitions(self.root.winfo_id())
        self.root.bind("<Map>", self._restore_window_style, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.tray = TrayIcon(self.root, self._close)
        self.root.withdraw()
        self.root.after(80, self._drain_events)
        self.root.after(DOCK_INTERVAL_MS, self._dock_to_client)
        self.root.after(100, self._sync_focus)
        self.engine.start()
        self.executor.submit(self.ddragon.load).add_done_callback(
            self._future_to_event("ddragon")
        )

    def _build(self) -> None:
        self.strip = tk.Frame(
            self.root,
            bg=BG,
            highlightbackground=FRAME_BORDER,
            highlightthickness=1,
        )
        self.strip.pack(fill="both", expand=True)
        self.row = tk.Frame(self.strip, bg=BG)
        self.row.place(x=0, y=0, relwidth=1, relheight=1)

    def _create_app_icon(self) -> tk.PhotoImage:
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(GOLD, to=(0, 0, 32, 32))
        icon.put(BG, to=(8, 6, 13, 25))
        icon.put(BG, to=(8, 20, 24, 25))
        return icon

    def _restore_window_style(self, _event: tk.Event | None = None) -> None:
        self.root.after_idle(self._apply_borderless_window_style)

    def _apply_borderless_window_style(self) -> None:
        if self.root.state() != "normal":
            return
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", False)
        show_in_taskbar(self.root.winfo_id())

    def _settings_snapshot(self) -> Settings:
        return replace(self.settings, preferred_champions=list(self.settings.preferred_champions))

    def _dock_to_client(self) -> None:
        window = league_client_window()
        self.client_hwnd = window[0] if window else None
        detected_rect = window[1] if window else None
        if detected_rect is not None:
            self.client_rect = detected_rect
            self.client_missing_since = None
        elif self.client_rect is not None:
            now = time.monotonic()
            if self.client_missing_since is None:
                self.client_missing_since = now
            elif now - self.client_missing_since >= TRANSIENT_STATE_GRACE_SECONDS:
                self.client_rect = None

        if self.client_rect is not None:
            geometry = top_bar_geometry(self.client_rect, BAR_HEIGHT)
            if self.root.geometry() != geometry:
                self.root.geometry(geometry)
        self._render_session()
        self.root.after(DOCK_INTERVAL_MS, self._dock_to_client)

    def _load_portraits(self, champion_ids: tuple[int, ...]) -> dict[int, str]:
        result: dict[int, str] = {}
        for champion_id in champion_ids:
            try:
                result[champion_id] = str(self.ddragon.portrait(champion_id))
            except (OSError, ValueError):
                result[champion_id] = ""
        return result

    def _request_portraits(self, champion_ids: Collection[int]) -> None:
        wanted = tuple(
            champion_id
            for champion_id in champion_ids
            if champion_id in self.summaries
            and champion_id not in self.portrait_paths
        )
        if not wanted or wanted == self.asset_batch:
            return
        self.asset_batch = wanted
        self.executor.submit(self._load_portraits, wanted).add_done_callback(
            self._future_to_event("portraits")
        )

    def _future_to_event(self, level: str):
        def callback(future: concurrent.futures.Future) -> None:
            try:
                payload = future.result()
            except Exception as exc:
                self.events.put(("asset_error", exc))
            else:
                self.events.put((level, payload))

        return callback

    def _bench(self) -> list[int]:
        if not self.session_state:
            return []
        return [int(value) for value in self.session_state.get("bench", [])]

    def _render_session(self) -> None:
        bench = self._bench()
        target = self.session_state.get("target") if self.session_state else None
        if bench:
            self._request_portraits(bench)

        ready = should_show_bar(
            self.current_phase,
            bench,
            self.client_rect,
            {
                champion_id
                for champion_id, path in self.portrait_paths.items()
                if path
            },
        )
        if not ready:
            self._hide_bar()
            return

        assert self.client_rect is not None
        bar_width = self.client_rect[2] - self.client_rect[0]
        slot_layout = bench_slot_layout(bar_width, len(bench))
        signature = (tuple(bench), target, tuple(slot_layout))
        if signature == self.last_render_signature:
            self._show_bar()
            return

        self.last_render_signature = signature
        for job in self.animation_jobs.values():
            self.root.after_cancel(job)
        self.animation_jobs.clear()
        self.cards.clear()
        for child in self.row.winfo_children():
            child.destroy()
        self.photos.clear()

        self.feedback = tk.Label(self.row, text=self._selection_text(target),
                                 bg=BG, fg=GOLD, font=("Microsoft YaHei UI", 10),
                                 anchor="w", wraplength=max(120, slot_layout[0][0] - 28))
        self.feedback.place(x=14, y=0, width=max(120, slot_layout[0][0] - 28), height=BAR_HEIGHT)

        for champion_id, (slot_left, slot_size) in zip(bench, slot_layout):
            path = self.portrait_paths[champion_id]
            try:
                source = tk.PhotoImage(file=path)
            except tk.TclError:
                self.portrait_paths[champion_id] = ""
                self.last_render_signature = None
                self._hide_bar()
                return

            divisor = max(
                1,
                round(max(source.width(), source.height()) / (slot_size - 4)),
            )
            photo = source.subsample(divisor, divisor)
            self.photos[champion_id] = photo

            border = GOLD if champion_id == target else IDLE_BORDER
            card = tk.Frame(
                self.row,
                bg=border,
                padx=2,
                pady=2,
            )
            card.place(
                x=slot_left,
                y=(BAR_HEIGHT - slot_size) // 2,
                width=slot_size,
                height=slot_size,
            )
            self.cards[champion_id] = card
            card.pack_propagate(False)
            button = tk.Button(
                card,
                image=photo,
                command=lambda value=champion_id: self._choose(value),
                bg=CARD,
                activebackground=ACTIVE_CARD,
                bd=0,
                highlightthickness=0,
                relief="flat",
                cursor="hand2",
                takefocus=True,
            )
            button.pack(fill="both", expand=True)
            button.bind("<Enter>", lambda _event, value=champion_id: self._hover(value, True))
            button.bind("<Leave>", lambda _event, value=champion_id: self._hover(value, False))
            if champion_id == target:
                badge = tk.Label(card, text="✓", bg=GOLD, fg=BG, font=("Segoe UI", 9, "bold"))
                badge.place(relx=1, x=-2, y=2, anchor="ne")
                badge.bind("<Button-1>", lambda _event, value=champion_id: self._choose(value))

        self._show_bar()

    def _choose(self, champion_id: int) -> None:
        current_target = (
            self.session_state.get("target") if self.session_state else None
        )
        target = None if current_target == champion_id else champion_id
        self.engine.set_target_champion(target)
        if self.session_state is not None:
            self.session_state["target"] = target
            self.last_render_signature = None
            self._render_session()
            if target is None and hasattr(self, "feedback"):
                self.feedback.configure(text="已取消目标 · 点击头像重新选择")
            self._animate_click(champion_id)

    def _selection_text(self, target: int | None) -> str:
        if target is None:
            return "点击头像设为目标"
        summary = self.summaries.get(target)
        name = summary.name if summary else f"英雄 {target}"
        if self.session_state and self.session_state.get("current") == target:
            return f"✓ 已换到 {name}"
        return f"✓ 已选 {name}\n再次点击取消"

    def _hover(self, champion_id: int, entered: bool) -> None:
        card = self.cards.get(champion_id)
        if card is not None and champion_id not in self.animation_jobs:
            target = self.session_state.get("target") if self.session_state else None
            card.configure(bg=GOLD if target == champion_id else ("#6b91ab" if entered else IDLE_BORDER))

    def _animate_click(self, champion_id: int, frame: int = 0) -> None:
        card = self.cards.get(champion_id)
        if card is None or not self.animations_enabled:
            return
        colors = ("#fff2be", "#ffe19a", "#efd080", "#dbb45c")
        if frame < len(colors):
            card.configure(bg=colors[frame])
            self.animation_jobs[champion_id] = self.root.after(
                55, lambda: self._animate_click(champion_id, frame + 1))
        else:
            self.animation_jobs.pop(champion_id, None)
            self._hover(champion_id, False)

    def _sync_focus(self) -> None:
        if self.bar_visible and self.client_hwnd is not None:
            sync_bar_z_order(self.root.winfo_id(), self.client_hwnd)
        self.root.after(100, self._sync_focus)

    def _show_bar(self) -> None:
        if self.bar_visible:
            return
        self.bar_visible = True
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.after_idle(self._apply_borderless_window_style)
        if self.client_hwnd is not None:
            sync_bar_z_order(self.root.winfo_id(), self.client_hwnd)

    def _hide_bar(self) -> None:
        if not self.bar_visible and self.root.state() == "withdrawn":
            return
        self.bar_visible = False
        self.root.withdraw()

    def _on_event(self, level: str, payload: Any) -> None:
        self.events.put((level, payload))

    def _drain_events(self) -> None:
        try:
            while True:
                level, payload = self.events.get_nowait()
                if level == "ddragon" and isinstance(payload, dict):
                    self.summaries = payload
                    self._request_portraits(self._bench())
                    if not self.preload_started:
                        self.preload_started = True
                        self.executor.submit(
                            self.ddragon.preload_portraits
                        ).add_done_callback(self._future_to_event("preload_done"))
                elif level == "preload_done" and isinstance(payload, dict):
                    self.portrait_paths.update(payload)
                    self.asset_batch = ()
                    self.last_render_signature = None
                    self._render_session()
                elif level == "portraits" and isinstance(payload, dict):
                    self.portrait_paths.update(payload)
                    self.asset_batch = ()
                    self.last_render_signature = None
                    self._render_session()
                elif level == "champ_select" and isinstance(payload, dict):
                    payload = dict(payload, target=self.engine.target_champion())
                    new_bench = list(payload.get("bench", []))
                    if not new_bench and self._bench():
                        now = time.monotonic()
                        if self.bench_empty_since is None:
                            self.bench_empty_since = now
                        if (
                            now - self.bench_empty_since
                            < TRANSIENT_STATE_GRACE_SECONDS
                        ):
                            continue
                    else:
                        self.bench_empty_since = None

                    old_signature = (
                        tuple(self._bench()),
                        self.session_state.get("current") if self.session_state else None,
                        self.session_state.get("target") if self.session_state else None,
                    )
                    new_signature = (
                        tuple(new_bench),
                        payload.get("current"),
                        payload.get("target"),
                    )
                    self.session_state = payload
                    if (
                        old_signature == new_signature
                        and self.last_render_signature is not None
                    ):
                        continue
                    self.last_render_signature = None
                    self._request_portraits(self._bench())
                    self._render_session()
                elif level == "target_cleared":
                    self.session_state = None
                    self.bench_empty_since = None
                    self.last_render_signature = None
                    self._hide_bar()
                elif level == "status":
                    self.current_phase = str(payload)
                    if self.current_phase != "ChampSelect":
                        self.session_state = None
                        self.bench_empty_since = None
                        self.last_render_signature = None
                    self._render_session()
                elif level in {"offline", "permission"}:
                    self.current_phase = None
                    self.client_rect = None
                    self.client_missing_since = None
                    self.session_state = None
                    self.bench_empty_since = None
                    self.last_render_signature = None
                    self._hide_bar()
                elif level == "asset_error":
                    self.asset_batch = ()
                    self._hide_bar()
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    def _close(self) -> None:
        self.tray.close()
        self.engine.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
