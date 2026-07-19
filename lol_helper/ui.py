from __future__ import annotations

import concurrent.futures
import queue
import tkinter as tk
from tkinter import messagebox
from typing import Any

from .automation import AutomationEngine
from .config import Settings
from .ddragon import ChampionSummary, DataDragon
from .lcu import is_elevated, restart_as_admin
from .logging_setup import configure_logging
from .window_docking import (
    enable_native_window_transitions,
    league_client_rect,
    show_in_taskbar,
    sidebar_geometry,
    system_window_animations_enabled,
)


BG = "#07121d"
PANEL = "#0d1d2a"
CARD = "#122838"
TEXT = "#e8f0f5"
MUTED = "#8296a5"
GOLD = "#c99b3d"
BLUE = "#2f8fc0"
GREEN = "#39b77a"
RED = "#d15b63"

PHASE_LABELS = {
    "None": "客户端待命",
    "Lobby": "房间准备中",
    "Matchmaking": "正在寻找对局",
    "ReadyCheck": "等待确认对局",
    "ChampSelect": "正在选择英雄",
    "GameStart": "正在进入游戏",
    "InProgress": "游戏进行中",
    "Reconnect": "正在重新连接",
    "WaitingForStats": "正在等待结算",
    "PreEndOfGame": "本局已结束",
    "EndOfGame": "查看对局结算",
}


class ModernScrollbar(tk.Canvas):
    """Compact dark scrollbar matching the sidebar instead of native Tk chrome."""

    def __init__(self, master: tk.Widget, command: Any):
        super().__init__(master, width=10, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self.drag_offset: float | None = None
        self.hovered = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _event: setattr(self, "drag_offset", None))

    def set(self, first: str | float, last: str | float) -> None:
        self.first, self.last = float(first), float(last)
        self._draw()

    def _metrics(self) -> tuple[float, float, float]:
        height = max(1, self.winfo_height())
        margin = 3.0
        track = max(1.0, height - margin * 2)
        top = margin + track * self.first
        bottom = margin + track * self.last
        if bottom - top < 34:
            center = (top + bottom) / 2
            top, bottom = center - 17, center + 17
            if top < margin:
                top, bottom = margin, margin + 34
            if bottom > height - margin:
                top, bottom = height - margin - 34, height - margin
        return top, bottom, track

    def _draw(self) -> None:
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        top, bottom, _track = self._metrics()
        color = GOLD if self.drag_offset is not None else "#527187" if self.hovered else "#304c60"
        x1, x2 = 2, max(7, self.winfo_width() - 2)
        radius = (x2 - x1) / 2
        self.create_rectangle(x1, top + radius, x2, bottom - radius, fill=color, outline="")
        self.create_oval(x1, top, x2, top + radius * 2, fill=color, outline="")
        self.create_oval(x1, bottom - radius * 2, x2, bottom, fill=color, outline="")

    def _enter(self, _event: tk.Event) -> None:
        self.hovered = True
        self._draw()

    def _leave(self, _event: tk.Event) -> None:
        self.hovered = False
        self._draw()

    def _press(self, event: tk.Event) -> None:
        top, bottom, _track = self._metrics()
        if top <= event.y <= bottom:
            self.drag_offset = event.y - top
        else:
            self.command("scroll", -1 if event.y < top else 1, "pages")
        self._draw()

    def _drag(self, event: tk.Event) -> None:
        if self.drag_offset is None:
            return
        height = max(1.0, float(self.winfo_height()))
        margin = 3.0
        track = max(1.0, height - margin * 2)
        thumb_fraction = max(0.0, self.last - self.first)
        target = (event.y - self.drag_offset - margin) / track
        target = max(0.0, min(target, 1.0 - thumb_fraction))
        self.command("moveto", target)


class HoverCard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.window: tk.Toplevel | None = None
        self.title: tk.Label | None = None
        self.body: tk.Label | None = None
        self.champion_id: int | None = None
        self.anchor_y = 0

    def show(self, widget: tk.Widget, summary: ChampionSummary) -> None:
        self.hide()
        self.champion_id = summary.champion_id
        self.anchor_y = widget.winfo_rooty()
        window = tk.Toplevel(self.root)
        self.window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        frame = tk.Frame(window, bg="#102536", highlightbackground=GOLD, highlightthickness=1, padx=12, pady=10)
        frame.pack(fill="both", expand=True)
        self.title = tk.Label(
            frame, text=f"{summary.name} · ID {summary.champion_id}",
            bg="#102536", fg="#f2d58a", font=("Microsoft YaHei UI", 11, "bold"), anchor="w",
        )
        self.title.pack(fill="x")
        subtitle = " / ".join(summary.tags)
        tk.Label(frame, text=f"{summary.title}　{subtitle}", bg="#102536", fg=MUTED,
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", pady=(2, 7))
        self.body = tk.Label(
            frame, text=summary.blurb or "正在读取技能信息……", bg="#102536", fg=TEXT,
            font=("Microsoft YaHei UI", 9), justify="left", anchor="nw", wraplength=390,
        )
        self.body.pack(fill="both")
        self._resize_and_place()
        window.lift()
        window.after_idle(lambda: window.lift() if window.winfo_exists() else None)

    def _resize_and_place(self) -> None:
        """Fit the tooltip to its latest content and keep it on screen."""
        if not self.window:
            return
        self.window.update_idletasks()
        tooltip_width = self.window.winfo_reqwidth()
        tooltip_height = self.window.winfo_reqheight()
        root_left = self.root.winfo_rootx()
        root_right = root_left + self.root.winfo_width()
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        if root_left >= tooltip_width + 10:
            x = root_left - tooltip_width - 10
        else:
            x = root_right + 10
            if x + tooltip_width > screen_width:
                x = max(4, screen_width - tooltip_width - 4)
        y = max(4, min(self.anchor_y, screen_height - tooltip_height - 8))
        self.window.geometry(f"{tooltip_width}x{tooltip_height}+{x}+{y}")

    def update(self, champion_id: int, detail: dict[str, Any]) -> None:
        if champion_id != self.champion_id or not self.body or not self.window:
            return
        lines = []
        passive = detail.get("passive", {})
        if passive.get("name"):
            lines.append(f"被动 · {passive['name']}\n{passive.get('description', '')}")
        for spell in detail.get("spells", []):
            lines.append(f"{spell['key']} · {spell['name']}\n{spell['description']}")
        self.body.configure(text="\n\n".join(lines) or detail.get("blurb", "暂无技能资料"))
        self._resize_and_place()

    def hide(self) -> None:
        self.champion_id = None
        if self.window:
            self.window.destroy()
            self.window = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LOL Helper")
        self.root.configure(bg=BG)
        self.root.geometry("330x720+20+60")
        self.root.minsize(310, 520)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self._app_icon = self._create_app_icon()
        self.root.iconphoto(True, self._app_icon)
        self.settings = Settings.load()
        self.settings.auto_accept = True
        self.settings.auto_pick = True
        self.settings.auto_bench_swap = True
        self.settings.preferred_champions = []
        self.settings.save()
        self.is_admin = is_elevated()

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="assets")
        self.ddragon = DataDragon()
        self.summaries: dict[int, ChampionSummary] = {}
        self.fallback_names: dict[int, str] = {}
        self.portrait_paths: dict[int, str] = {}
        self.photos: dict[int, tk.PhotoImage] = {}
        self.details_loading: set[int] = set()
        self.asset_batch: tuple[int, ...] = ()
        self.session_state: dict[str, Any] | None = None
        self.last_render_signature: tuple[Any, ...] | None = None
        self.preload_started = False
        self.position_manually_changed = False
        self.initial_dock_completed = False
        self.game_in_progress = False
        self.hidden_for_game = False
        self.drag_origin: tuple[int, int, int, int] | None = None
        self._minimize_requested = False
        self._restore_animation_id: str | None = None
        self.hover = HoverCard(root)
        self.engine = AutomationEngine(self._settings_snapshot, self._on_event, configure_logging())

        self._build()
        self.root.update_idletasks()
        show_in_taskbar(self.root.winfo_id())
        enable_native_window_transitions(self.root.winfo_id())
        self.root.bind("<Map>", self._restore_window_style, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(80, self._drain_events)
        self.root.after(500, self._dock_to_client)
        self.engine.start()
        self.executor.submit(self._load_ddragon).add_done_callback(self._future_to_event("ddragon"))

    def _build(self) -> None:
        header = tk.Frame(self.root, bg="#091722", height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self._start_drag)
        header.bind("<B1-Motion>", self._drag)
        brand = tk.Frame(header, bg="#091722")
        brand.pack(side="left", padx=14, pady=9)
        tk.Label(brand, text="L", bg=GOLD, fg=BG, font=("Segoe UI", 11, "bold"), width=2).pack(side="left")
        title = tk.Frame(brand, bg="#091722")
        title.pack(side="left", padx=8)
        tk.Label(title, text="LOL HELPER", bg="#091722", fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.phase_label = tk.Label(title, text="正在连接客户端", bg="#091722", fg=MUTED,
                                    font=("Microsoft YaHei UI", 8))
        self.phase_label.pack(anchor="w")
        tk.Button(header, text="×", command=self._close, bg="#091722", fg=MUTED,
                  activebackground="#7d2530", activeforeground="white", bd=0,
                  font=("Segoe UI", 14), cursor="hand2").pack(side="right", padx=(0, 8))
        tk.Button(header, text="—", command=self._minimize, bg="#091722", fg=MUTED,
                  activebackground=PANEL, activeforeground="white", bd=0,
                  font=("Segoe UI", 11), cursor="hand2").pack(side="right")

        target = tk.Frame(self.root, bg=PANEL, padx=14, pady=11,
                          highlightbackground="#173247", highlightthickness=1)
        target.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(target, text="本局目标", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        self.target_label = tk.Label(target, text="等待进入选角", bg=PANEL, fg=TEXT,
                                     font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        self.target_label.pack(fill="x", pady=(2, 0))
        self.target_hint = tk.Label(target, text="进入选角后点击英雄头像", bg=PANEL, fg=MUTED,
                                    font=("Microsoft YaHei UI", 8), anchor="w")
        self.target_hint.pack(fill="x", pady=(3, 0))

        automation = tk.Frame(self.root, bg=BG)
        automation.pack(fill="x", padx=12, pady=(3, 0))
        for text in ("● 自动接受", "● 自动选择", "● 自动交换"):
            tk.Label(automation, text=text, bg=BG, fg=GREEN,
                     font=("Microsoft YaHei UI", 8)).pack(side="left", expand=True)

        legend = tk.Frame(self.root, bg=BG)
        legend.pack(fill="x", padx=14, pady=(6, 0))
        for text, color in (("当前", GREEN), ("目标", GOLD), ("公共池", BLUE)):
            tk.Label(legend, text=f"● {text}", bg=BG, fg=color,
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(0, 12))

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ModernScrollbar(self.root, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=5)
        self.canvas.pack(fill="both", expand=True, padx=(10, 2), pady=4)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._sync_canvas_layout)
        self.canvas.bind("<Configure>", self._sync_canvas_layout)
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        footer = tk.Frame(self.root, bg="#091722", padx=12, pady=8)
        footer.pack(fill="x")
        self.connection_dot = tk.Label(footer, text="●", bg="#091722", fg=RED, font=("Segoe UI", 9))
        self.connection_dot.pack(side="left")
        self.connection_label = tk.Label(footer, text="等待客户端", bg="#091722", fg=MUTED,
                                         font=("Microsoft YaHei UI", 8))
        self.connection_label.pack(side="left", padx=5)
        self.admin_button = tk.Button(footer, text="管理员重启", command=self._restart_admin,
                                      bg=PANEL, fg="#f2d58a", activebackground=CARD,
                                      activeforeground="white", bd=0, padx=8, cursor="hand2")
        tk.Button(footer, text="诊断", command=self._diagnostic, bg="#091722", fg=MUTED,
                  activebackground=PANEL, activeforeground="white", bd=0, cursor="hand2").pack(side="right")
        self._show_empty("启动后会自动接受对局\n进入选角即可点击英雄头像")

    def _create_app_icon(self) -> tk.PhotoImage:
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(GOLD, to=(0, 0, 32, 32))
        icon.put(BG, to=(8, 6, 13, 25))
        icon.put(BG, to=(8, 20, 24, 25))
        return icon

    def _restore_window_style(self, _event: tk.Event | None = None) -> None:
        if not self._minimize_requested:
            self.root.after_idle(self._apply_borderless_window_style)
            return

        # A restored Tk window briefly has its native title bar. Make that
        # intermediate frame transparent, then reveal the borderless window.
        self._minimize_requested = False
        self.root.attributes("-alpha", 0.0)
        self.root.after_idle(self._finish_restore)

    def _finish_restore(self) -> None:
        self._apply_borderless_window_style()
        self.root.update_idletasks()
        self.root.lift()
        if system_window_animations_enabled():
            self._animate_restore_opacity()
        else:
            self.root.attributes("-alpha", 1.0)

    def _animate_restore_opacity(self, step: int = 0) -> None:
        """Quick ease-out reveal that masks the custom-frame handoff."""
        steps = 10
        progress = min(1.0, step / steps)
        opacity = 1.0 - (1.0 - progress) ** 3
        self.root.attributes("-alpha", opacity)
        if step < steps:
            self._restore_animation_id = self.root.after(
                12, self._animate_restore_opacity, step + 1
            )
        else:
            self._restore_animation_id = None

    def _apply_borderless_window_style(self) -> None:
        if self.root.state() != "normal":
            return
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", not self.game_in_progress)
        show_in_taskbar(self.root.winfo_id())

    def _minimize(self) -> None:
        if self.root.state() != "normal" or self._minimize_requested:
            return
        self.hover.hide()
        if self._restore_animation_id is not None:
            self.root.after_cancel(self._restore_animation_id)
            self._restore_animation_id = None
            self.root.attributes("-alpha", 1.0)
        self._minimize_requested = True
        self.root.overrideredirect(False)
        show_in_taskbar(self.root.winfo_id())
        enable_native_window_transitions(self.root.winfo_id())
        self.root.update_idletasks()
        self.root.iconify()

    def _apply_game_phase_window_policy(self, phase: str) -> None:
        in_game = phase in {"GameStart", "InProgress", "Reconnect"}
        if in_game == self.game_in_progress:
            return
        self.game_in_progress = in_game
        if in_game:
            self._clear_current_match()
            self.root.attributes("-topmost", False)
            self.root.withdraw()
            self.hidden_for_game = True
        elif self.hidden_for_game:
            self.hidden_for_game = False
            self.root.deiconify()
            self.root.after_idle(self._apply_borderless_window_style)

    def _clear_current_match(self) -> None:
        """Discard the completed champ-select view so the next match starts fresh."""
        self.hover.hide()
        self.session_state = None
        self.last_render_signature = None
        self.asset_batch = ()
        self.target_label.configure(
            text="等待下一次进入选角" if self.game_in_progress else "等待进入选角",
            fg=TEXT,
        )
        self.target_hint.configure(
            text="下一局选角时会自动刷新" if self.game_in_progress else "进入选角后点击英雄头像"
        )
        message = (
            "本局已开始\n等待下一次进入选角"
            if self.game_in_progress
            else "启动后会自动接受对局\n进入选角即可点击英雄头像"
        )
        self._show_empty(message)

    def _sync_canvas_layout(self, _event: tk.Event | None = None) -> None:
        """Pin short content to the top; only create a scroll range when needed."""
        viewport_width = max(1, self.canvas.winfo_width())
        viewport_height = max(1, self.canvas.winfo_height())
        required_height = max(1, self.content.winfo_reqheight())
        content_height = max(viewport_height, required_height)
        self.canvas.itemconfigure(
            self.content_window,
            width=viewport_width,
            height=content_height,
        )
        self.canvas.configure(scrollregion=(0, 0, viewport_width, content_height))

    def _settings_snapshot(self) -> Settings:
        return Settings(True, True, True, [], self.settings.poll_interval_ms)

    def _start_drag(self, event: tk.Event) -> None:
        self.position_manually_changed = True
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if self.drag_origin:
            sx, sy, wx, wy = self.drag_origin
            self.root.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def _dock_to_client(self) -> None:
        if self.position_manually_changed or self.initial_dock_completed:
            return
        rect = league_client_rect()
        if rect:
            self.root.geometry(sidebar_geometry(rect))
            self.initial_dock_completed = True
            return
        self.root.after(2000, self._dock_to_client)

    def _load_ddragon(self) -> dict[int, ChampionSummary]:
        return self.ddragon.load()

    def _future_to_event(self, level: str, extra: Any = None):
        def callback(future: concurrent.futures.Future) -> None:
            try:
                self.events.put((level, (extra, future.result()) if extra is not None else future.result()))
            except Exception as exc:
                self.events.put(("asset_error", str(exc)))
        return callback

    def _request_portraits(self, champion_ids: tuple[int, ...]) -> None:
        wanted = tuple(value for value in champion_ids if value in self.summaries and value not in self.portrait_paths)
        if not wanted:
            self._render_session()
            return
        if wanted == self.asset_batch:
            return
        self.asset_batch = wanted

        def download() -> dict[int, str]:
            result = {}
            for champion_id in wanted:
                try:
                    result[champion_id] = str(self.ddragon.portrait(champion_id))
                except Exception:
                    # Remember a failed item for this run so a network problem
                    # cannot create a tight retry loop every monitor tick.
                    result[champion_id] = ""
            return result

        self._show_empty("正在读取本局英雄头像……")
        self.executor.submit(download).add_done_callback(self._future_to_event("portraits"))

    def _show_empty(self, text: str) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        tk.Label(self.content, text=text, bg=BG, fg=MUTED, justify="center",
                 font=("Microsoft YaHei UI", 9), pady=35).pack(fill="x")

    def _render_session(self) -> None:
        if not self.session_state:
            return
        cards = [int(value) for value in self.session_state.get("cards", [])]
        bench = [int(value) for value in self.session_state.get("bench", [])]
        current = self.session_state.get("current")
        target = self.session_state.get("target")
        signature = (tuple(cards), tuple(bench), current, target, len(self.summaries))
        if signature == self.last_render_signature:
            return
        all_ids = tuple(dict.fromkeys(([int(current)] if current else []) + cards + bench))
        missing = [value for value in all_ids if value in self.summaries and value not in self.portrait_paths]
        if missing:
            self._request_portraits(all_ids)
            return
        self.last_render_signature = signature
        for child in self.content.winfo_children():
            child.destroy()
        if target:
            self.target_label.configure(text=self._name(int(target)), fg="#f2d58a")
            self.target_hint.configure(text="再次点击该英雄可取消目标")
        else:
            self.target_label.configure(text="点击一个英雄头像", fg=TEXT)
            self.target_hint.configure(text="助手会自动选择或从公共池交换")

        own = list(dict.fromkeys(([int(current)] if current else []) + cards))
        self._section("我的英雄", own, current, target, "own")
        self._section("公共英雄池", bench, current, target, "bench")
        if not own and not bench:
            self._show_empty("当前会话尚未返回英雄数据\n可点击底部“诊断”保存会话")
        self.content.update_idletasks()
        self._sync_canvas_layout()
        self.canvas.yview_moveto(0)

    def _section(self, title: str, champion_ids: list[int], current: Any, target: Any, kind: str) -> None:
        if not champion_ids:
            return
        tk.Label(self.content, text=title.upper(), bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8, "bold"), anchor="w").pack(fill="x", pady=(9, 6))
        grid = tk.Frame(self.content, bg=BG)
        grid.pack(fill="x")
        for index, champion_id in enumerate(champion_ids):
            path = self.portrait_paths.get(champion_id)
            if not path:
                continue
            try:
                photo = tk.PhotoImage(file=path).subsample(2, 2)
            except tk.TclError:
                continue
            self.photos[champion_id] = photo
            border = GOLD if champion_id == target else GREEN if champion_id == current else BLUE if kind == "bench" else "#284354"
            card = tk.Frame(grid, bg=border, padx=2, pady=2)
            card.grid(row=index // 3, column=index % 3, padx=4, pady=4, sticky="n")
            button = tk.Button(
                card, image=photo, text=self._name(champion_id), compound="top",
                command=lambda value=champion_id: self._choose(value),
                bg=CARD, fg=TEXT, activebackground="#1a3a4f", activeforeground="white",
                bd=0, padx=4, pady=4, width=76, cursor="hand2",
                font=("Microsoft YaHei UI", 8),
            )
            button.pack()
            button.bind("<Enter>", lambda _e, value=champion_id, widget=button: self._hover_enter(widget, value))
            button.bind("<Leave>", lambda _e: self.hover.hide())

    def _name(self, champion_id: int) -> str:
        summary = self.summaries.get(champion_id)
        return summary.name if summary else self.fallback_names.get(champion_id, str(champion_id))

    def _choose(self, champion_id: int) -> None:
        current_target = self.session_state.get("target") if self.session_state else None
        target = None if current_target == champion_id else champion_id
        self.engine.set_target_champion(target)
        if self.session_state is not None:
            self.session_state["target"] = target
            self.last_render_signature = None
            self._render_session()

    def _hover_enter(self, widget: tk.Widget, champion_id: int) -> None:
        summary = self.summaries.get(champion_id)
        if not summary:
            return
        self.hover.show(widget, summary)
        if champion_id in self.details_loading:
            return
        self.details_loading.add(champion_id)
        self.executor.submit(self.ddragon.details, champion_id).add_done_callback(
            self._future_to_event("details", champion_id)
        )

    def _diagnostic(self) -> None:
        try:
            path = self.engine.save_diagnostic()
            messagebox.showinfo("诊断已保存", str(path))
        except RuntimeError as exc:
            messagebox.showinfo("暂无会话", str(exc))

    def _restart_admin(self) -> None:
        if restart_as_admin():
            self._close()
        else:
            messagebox.showerror("无法重启", "管理员启动被取消，或程序已经拥有管理员权限。")

    def _on_event(self, level: str, payload: Any) -> None:
        self.events.put((level, payload))

    def _drain_events(self) -> None:
        try:
            while True:
                level, payload = self.events.get_nowait()
                if level == "ddragon" and isinstance(payload, dict):
                    self.summaries = payload
                    if not self.preload_started:
                        self.preload_started = True
                        self.connection_label.configure(text="正在预载全部头像…")
                        self.executor.submit(self.ddragon.preload_portraits).add_done_callback(
                            self._future_to_event("preload_done")
                        )
                    if self.session_state:
                        ids = tuple(dict.fromkeys(
                            list(self.session_state.get("cards", [])) + list(self.session_state.get("bench", []))
                        ))
                        self._request_portraits(ids)
                elif level == "portraits" and isinstance(payload, dict):
                    self.portrait_paths.update(payload)
                    self.asset_batch = ()
                    self.last_render_signature = None
                    self._render_session()
                elif level == "preload_done" and isinstance(payload, tuple):
                    completed, total = payload
                    self.connection_label.configure(text=f"头像已缓存 {completed}/{total}")
                elif level == "details" and isinstance(payload, tuple):
                    champion_id, detail = payload
                    self.details_loading.discard(champion_id)
                    self.hover.update(champion_id, detail)
                elif level == "champions" and isinstance(payload, list):
                    self.fallback_names = {int(item["id"]): str(item["name"]) for item in payload}
                elif level == "champ_select" and isinstance(payload, dict):
                    old = self.session_state
                    self.session_state = payload
                    old_signature = None if old is None else (
                        tuple(old.get("cards", [])), tuple(old.get("bench", [])),
                        old.get("current"), old.get("target"),
                    )
                    new_signature = (
                        tuple(payload.get("cards", [])), tuple(payload.get("bench", [])),
                        payload.get("current"), payload.get("target"),
                    )
                    if old_signature == new_signature and self.last_render_signature is not None:
                        continue
                    ids = tuple(dict.fromkeys(
                        ([payload["current"]] if payload.get("current") else [])
                        + list(payload.get("cards", [])) + list(payload.get("bench", []))
                    ))
                    self._request_portraits(ids)
                elif level == "target_cleared":
                    self._clear_current_match()
                elif level in {"status", "offline", "permission"}:
                    text = str(payload)
                    phase = text.replace("客户端在线 · ", "")
                    self.phase_label.configure(text=PHASE_LABELS.get(phase, phase))
                    self.connection_label.configure(
                        text=("已连接 · 管理员" if self.is_admin else "已连接")
                        if level == "status"
                        else "需要管理员权限" if level == "permission"
                        else "等待客户端"
                    )
                    self.connection_dot.configure(fg=GREEN if level == "status" else GOLD if level == "permission" else "#d15b63")
                    if level == "status":
                        self._apply_game_phase_window_policy(phase)
                        if self.admin_button.winfo_ismapped():
                            self.admin_button.pack_forget()
                    if level == "permission" and not self.admin_button.winfo_ismapped():
                        self.admin_button.pack(side="right", padx=7)
                elif level == "asset_error":
                    self.connection_label.configure(text="素材载入失败")
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    def _close(self) -> None:
        self.hover.hide()
        self.engine.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
