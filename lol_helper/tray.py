from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes
from typing import Callable

from .window_docking import native_window_handle


user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

WM_APP = 0x8000
WM_COMMAND = 0x0111
WM_GETICON = 0x007F
WM_NULL = 0x0000
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
ICON_SMALL = 0
ICON_SMALL2 = 2
GWLP_WNDPROC = -4

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NOTIFYICON_VERSION_4 = 4

IMAGE_ICON = 1
IDI_APPLICATION = 32512
LR_SHARED = 0x00008000

MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

TRAY_MESSAGE = WM_APP + 1
EXIT_COMMAND = 1001


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

user32.SetWindowLongPtrW.argtypes = (
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_void_p,
)
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes = (
    ctypes.c_void_p,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
user32.CallWindowProcW.restype = ctypes.c_ssize_t
user32.LoadImageW.argtypes = (
    wintypes.HINSTANCE,
    ctypes.c_void_p,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
)
user32.LoadImageW.restype = wintypes.HANDLE
shell32.Shell_NotifyIconW.argtypes = (
    wintypes.DWORD,
    ctypes.POINTER(NOTIFYICONDATAW),
)
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayIcon:
    """Native notification-area icon with a single, dependable exit action."""

    def __init__(self, root: tk.Tk, on_exit: Callable[[], None]):
        self.root = root
        self.on_exit = on_exit
        self.hwnd = native_window_handle(root.winfo_id())
        self.menu = user32.CreatePopupMenu()
        user32.AppendMenuW(self.menu, MF_STRING, EXIT_COMMAND, "退出")
        self.icon_handle = (
            user32.SendMessageW(self.hwnd, WM_GETICON, ICON_SMALL2, 0)
            or user32.SendMessageW(self.hwnd, WM_GETICON, ICON_SMALL, 0)
            or user32.LoadImageW(
                None,
                ctypes.c_void_p(IDI_APPLICATION),
                IMAGE_ICON,
                16,
                16,
                LR_SHARED,
            )
        )
        self.taskbar_created_message = user32.RegisterWindowMessageW(
            "TaskbarCreated"
        )
        self._wndproc = WNDPROC(self._window_proc)
        self._original_wndproc = user32.SetWindowLongPtrW(
            self.hwnd,
            GWLP_WNDPROC,
            ctypes.cast(self._wndproc, ctypes.c_void_p),
        )
        self._closing = False
        self._add_icon()

    def _notification_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = TRAY_MESSAGE
        data.hIcon = self.icon_handle
        data.szTip = "LOL Helper · 右键退出"
        return data

    def _add_icon(self) -> None:
        data = self._notification_data()
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            data.uTimeoutOrVersion = NOTIFYICON_VERSION_4
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(data))

    def _show_menu(self) -> None:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)
        command = user32.TrackPopupMenu(
            self.menu,
            TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
            point.x,
            point.y,
            0,
            self.hwnd,
            None,
        )
        if command == EXIT_COMMAND:
            self._request_exit()
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)

    def _request_exit(self) -> None:
        if not self._closing:
            self._closing = True
            self.root.after(0, self.on_exit)

    def _window_proc(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == self.taskbar_created_message:
            self._add_icon()
            return 0
        if message == TRAY_MESSAGE:
            event = int(lparam) & 0xFFFF
            if event == WM_RBUTTONUP:
                self._show_menu()
                return 0
            if event == WM_LBUTTONDBLCLK:
                self._request_exit()
                return 0
        if message == WM_COMMAND and int(wparam) & 0xFFFF == EXIT_COMMAND:
            self._request_exit()
            return 0
        return user32.CallWindowProcW(
            ctypes.c_void_p(self._original_wndproc),
            hwnd,
            message,
            wparam,
            lparam,
        )

    def close(self) -> None:
        data = self._notification_data()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        if self.menu:
            user32.DestroyMenu(self.menu)
            self.menu = None
        if self._original_wndproc:
            user32.SetWindowLongPtrW(
                self.hwnd,
                GWLP_WNDPROC,
                ctypes.c_void_p(self._original_wndproc),
            )
            self._original_wndproc = 0
