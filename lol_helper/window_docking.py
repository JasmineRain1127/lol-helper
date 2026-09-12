from __future__ import annotations

import ctypes
from ctypes import wintypes


# Keep geometry helpers importable for tests on non-Windows hosts.
user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
if user32 is not None:
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = (wintypes.HWND,)
    user32.GetParent.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.SetWindowPos.restype = wintypes.BOOL

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
    style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW | 0x08000000  # WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(
        hwnd,
        0,
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


def league_client_window() -> tuple[int, tuple[int, int, int, int]] | None:
    matches: list[tuple[int, tuple[int, int, int, int]]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
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
                matches.append((hwnd, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return max(matches, key=lambda value: (value[1][2] - value[1][0]) * (value[1][3] - value[1][1])) if matches else None


def top_bar_geometry(rect: tuple[int, int, int, int], height: int = 72) -> str:
    """Attach a horizontal bar immediately above the League client."""
    left, top, right, _bottom = rect
    width = max(1, right - left)
    bar_height = max(1, height)
    return f"{width}x{bar_height}{left:+d}{top - bar_height:+d}"


def league_client_rect() -> tuple[int, int, int, int] | None:
    window = league_client_window()
    return window[1] if window else None


def sync_bar_z_order(tk_window_id: int, client_hwnd: int) -> None:
    """Place the bar directly above the client without activating either window.

    Use the ordinary Z-order band, never HWND_TOPMOST. In the background,
    insert after the window immediately above the client so other apps stay above us.
    """
    hwnd = native_window_handle(tk_window_id)
    if not user32.IsWindow(client_hwnd) or user32.IsIconic(client_hwnd):
        return
    foreground = user32.GetForegroundWindow()
    if foreground in (client_hwnd, hwnd):
        insert_after = 0  # HWND_TOP in the non-topmost band
    else:
        insert_after = user32.GetWindow(client_hwnd, 3)  # GW_HWNDPREV
        if insert_after == hwnd:
            return
        # A topmost predecessor would promote us into its band. Stay below it.
        if insert_after and user32.GetWindowLongW(insert_after, GWL_EXSTYLE) & 0x8:
            insert_after = -2  # HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
