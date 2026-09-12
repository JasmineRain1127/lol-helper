from __future__ import annotations

import tkinter as tk
from typing import Any


class StartupNotice:
    """Brief, non-modal confirmation that the background helper is running."""

    def __init__(self, root: tk.Tk, duration_ms: int = 8000):
        self.window = tk.Toplevel(root)
        self.window.title("LOL Helper · 已启动")
        self.window.configure(bg="#07131f", padx=24, pady=20)
        self.window.resizable(False, False)
        self.closed = False
        self._timer: str | None = None
        tk.Label(self.window, text="✓ 助手已启动", bg="#07131f", fg="#c99b3d",
                 font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        self.status = tk.Label(self.window, text="正在连接英雄联盟客户端…",
                               bg="#07131f", fg="#edf3f7", wraplength=390, height=2, anchor="w",
                               justify="left", font=("Microsoft YaHei UI", 10))
        self.status.pack(anchor="w", pady=(12, 6))
        tk.Label(self.window,
                 text="进入大乱斗选角后自动显示英雄横条。\n"
                      "助手会继续在后台运行；退出请右键系统托盘图标。\n"
                      "此提示将在 8 秒后自动关闭。",
                 bg="#07131f", fg="#a0b3c2", justify="left",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(0, 14))
        tk.Button(self.window, text="知道了，后台运行", command=self.close,
                  bg="#17394f", fg="#edf3f7", padx=12, pady=5).pack(anchor="e")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, (self.window.winfo_screenwidth() - width) // 2)
        y = max(0, (self.window.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self._timer = self.window.after(duration_ms, self.close)

    def update_status(self, level: str, payload: Any) -> None:
        if self.closed:
            return
        if level in {"offline", "permission"}:
            message = str(payload)
        elif level == "status":
            message = "已连接客户端 · 进入大乱斗选角后显示横条"
            if payload == "ChampSelect":
                message = "已进入选角 · 正在准备英雄横条"
        elif level == "error":
            message = "连接暂时异常，助手仍在运行并将继续重试"
        else:
            return
        self.status.configure(text=message)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._timer is not None:
            self.window.after_cancel(self._timer)
            self._timer = None
        self.window.destroy()
