from __future__ import annotations

import ctypes
from ctypes import wintypes


user32 = ctypes.windll.user32
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.GetParent.argtypes = (wintypes.HWND,)
user32.GetParent.restype = wintypes.HWND

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
GA_ROOT = 2
SPI_GETCLIENTAREAANIMATION = 0x1042
DWMWA_TRANSITIONS_FORCEDISABLED = 3


def native_window_handle(tk_window_id: int) -> int:
    """Return Tk's outermost native window instead of its child drawing window."""
    return user32.GetAncestor(tk_window_id, GA_ROOT) or user32.GetParent(tk_window_id) or tk_window_id


def show_in_taskbar(tk_window_id: int) -> None:
    """Expose a borderless Tk window as a normal Windows taskbar app."""
    hwnd = native_window_handle(tk_window_id)
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def enable_native_window_transitions(tk_window_id: int) -> None:
    """Keep DWM minimize/restore transitions enabled for the custom-framed window."""
    try:
        disabled = wintypes.BOOL(False)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            native_window_handle(tk_window_id),
            DWMWA_TRANSITIONS_FORCEDISABLED,
            ctypes.byref(disabled),
            ctypes.sizeof(disabled),
        )
    except (AttributeError, OSError):
        # DWM is unavailable on older/minimal Windows environments.
        pass


def system_window_animations_enabled() -> bool:
    """Respect the user's Windows 'show animations' accessibility preference."""
    enabled = wintypes.BOOL()
    if not user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
    ):
        return True
    return bool(enabled.value)


def league_client_rect() -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.lower()
        if "league of legends" not in title and "英雄联盟" not in title:
            return True
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width >= 600 and height >= 400:
                matches.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return max(matches, key=lambda value: (value[2] - value[0]) * (value[3] - value[1])) if matches else None


def sidebar_geometry(rect: tuple[int, int, int, int], width: int = 330) -> str:
    left, top, right, bottom = rect
    screen_width = user32.GetSystemMetrics(0)
    screen_height = user32.GetSystemMetrics(1)
    height = max(520, min(bottom - top, screen_height - max(top, 0)))
    x = right + 6 if right + width + 6 <= screen_width else max(0, left - width - 6)
    y = max(0, min(top, screen_height - height))
    return f"{width}x{height}+{x}+{y}"
