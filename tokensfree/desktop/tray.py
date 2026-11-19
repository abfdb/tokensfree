"""Windows system tray icon via ctypes (Shell_NotifyIcon), stdlib only.

The tray runs its own thread with a hidden window and a Win32 message loop.
Menu clicks are delivered as plain strings to the callback so the Tkinter main
thread can apply them without touching Win32 state.
"""
import ctypes
import sys
import threading
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_APP = 0x8000
TRAY_MESSAGE = WM_APP + 1
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IDI_APPLICATION = 32512
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080

MENU_COMMANDS = {
    1: "show",
    2: "console",
    3: "toggle-gateway",
    4: "exit",
}

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, ctypes.c_void_p,
    ]
    _user32.TrackPopupMenu.restype = ctypes.c_uint
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = _LRESULT
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

    _WINDOW_CLASS = "TokensfreeTrayWindow"


class TrayIcon:
    """Create a tray icon with a small right-click menu.

    start() returns True once the icon is installed; stop() removes it and joins
    the message-loop thread. Menu actions are string commands: show/console/
    toggle-gateway/exit.
    """

    def __init__(self, *, tooltip="tokensfree 控制台", on_command=None):
        self._tooltip = tooltip
        self._on_command = on_command
        self._thread = None
        self._ready = threading.Event()
        self._hwnd = None
        self._started = False
        self._error = None
        self._wndproc_ref = None
        self._nid = None

    @property
    def error(self):
        return self._error

    def start(self, timeout=5.0):
        if not IS_WINDOWS:
            self._error = "system tray is only implemented on Windows"
            return False
        if self._started:
            return True
        self._thread = threading.Thread(target=self._run, name="tokensfree-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._started

    def stop(self, timeout=5.0):
        hwnd = self._hwnd
        if hwnd:
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout)
        self._started = False

    def _run(self):
        try:
            instance = _kernel32.GetModuleHandleW(None)
            self._wndproc_ref = _WNDPROC(self._wndproc)
            window_class = _WNDCLASSW()
            window_class.lpfnWndProc = self._wndproc_ref
            window_class.hInstance = instance
            window_class.lpszClassName = _WINDOW_CLASS
            _user32.RegisterClassW(ctypes.byref(window_class))
            hwnd = _user32.CreateWindowExW(
                0, _WINDOW_CLASS, "tokensfree", 0, 0, 0, 0, 0, None, None, instance, None
            )
            if not hwnd:
                self._error = "CreateWindowExW failed (%d)" % ctypes.get_last_error()
                return
            self._hwnd = hwnd
            if not self._add_icon():
                self._error = "Shell_NotifyIconW failed"
                _user32.DestroyWindow(hwnd)
                self._hwnd = None
                return
            self._started = True
            self._ready.set()
            message = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:  # the tray is optional; never crash the UI
            self._error = str(error)
        finally:
            self._started = False
            self._ready.set()

    def _add_icon(self):
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = TRAY_MESSAGE
        nid.hIcon = _user32.LoadIconW(None, IDI_APPLICATION)
        nid.szTip = self._tooltip
        self._nid = nid
        return bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove_icon(self):
        if self._nid is not None:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == TRAY_MESSAGE:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, WM_LBUTTONDBLCLK):
                self._show_menu()
                return 0
        if message == WM_CLOSE:
            self._remove_icon()
            _user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self):
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        try:
            _user32.AppendMenuW(menu, MF_STRING, 1, "显示控制面板")
            _user32.AppendMenuW(menu, MF_STRING, 2, "打开网页控制台")
            _user32.AppendMenuW(menu, MF_STRING, 3, "启动/停止网关")
            _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(menu, MF_STRING, 4, "退出")
            point = _POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            _user32.SetForegroundWindow(self._hwnd)
            command = _user32.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY, point.x, point.y, 0, self._hwnd, None
            )
            if command:
                action = MENU_COMMANDS.get(command)
                if action and self._on_command is not None:
                    self._on_command(action)
        finally:
            _user32.DestroyMenu(menu)
