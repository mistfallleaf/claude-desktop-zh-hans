"""Claude Desktop 图标组件。

让任务栏、开始菜单和桌面入口始终显示官方 Claude 图标，与汉化后的应用身份一致。
原理：为 Claude 窗口设置窗口级大小图标，并补全任务栏重启所需的 AppUserModel
属性（图标 / 命令 / 显示名 / 独立 ID）。窗口重建后会自动重新应用。

- 纯 ctypes 实现，无第三方依赖；不发任何网络请求。
- 每 5 秒检查一次，属性已是目标值时完全不写入（空闲几乎零开销）。
- 单实例运行（命名 Mutex），可重复启动不会叠加。

用法：
  icon_guard.pyw                 # 常驻守护模式（安装脚本用这个）
  icon_guard.pyw --once          # 只修一次并打印 JSON 报告，然后退出
  icon_guard.pyw --launch        # 先通过 AppsFolder 启动 Claude，再守护
  icon_guard.pyw --icon <path>   # 指定 ico 文件（默认取同目录 claude.ico）
"""
import argparse
import ctypes
import json
import os
import sys
from pathlib import Path
import time
import uuid
from ctypes import wintypes as w

BASE = Path(__file__).resolve().parent
APP_ID = "Claude.Localized.IconGuard"
# 官方 MSIX 版的 AppsFolder 入口（publisher id 稳定，与版本号无关）
DEFAULT_PACKAGE_ID = "Claude_pzs8sxrjxfjjc!Claude"
MUTEX_NAME = "Local\\ClaudeDesktopIconGuard"
LOG_NAME = "icon_guard.log"

u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
shell = ctypes.WinDLL("shell32", use_last_error=True)
ole = ctypes.OleDLL("ole32")
ENUM = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class KEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", w.DWORD)]


class PVUnion(ctypes.Union):
    _fields_ = [("text", ctypes.c_void_p), ("padding", ctypes.c_ubyte * 16)]


class PV(ctypes.Structure):
    _fields_ = [("vt", w.USHORT), ("r1", w.USHORT), ("r2", w.USHORT),
                ("r3", w.USHORT), ("v", PVUnion)]


u.EnumWindows.argtypes = [ENUM, w.LPARAM]
u.IsWindowVisible.argtypes = [w.HWND]
u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                  w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
u.SendMessageTimeoutW.restype = w.LPARAM
u.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int,
                         ctypes.c_int, w.UINT]
u.LoadImageW.restype = w.HANDLE
u.RedrawWindow.argtypes = [w.HWND, ctypes.c_void_p, w.HANDLE, w.UINT]
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                        ctypes.POINTER(w.DWORD)]
k.CloseHandle.argtypes = [w.HANDLE]
k.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
k.CreateMutexW.restype = w.HANDLE
shell.SHGetPropertyStoreForWindow.argtypes = [w.HWND, ctypes.POINTER(GUID),
                                             ctypes.POINTER(ctypes.c_void_p)]
shell.SHGetPropertyStoreForWindow.restype = ctypes.c_long
ole.PropVariantClear.argtypes = [ctypes.POINTER(PV)]
FMT = GUID.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3")
IID = GUID.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")


def log(path, message):
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass


def process_path(pid):
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(handle, 0, buf,
                                                        ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(handle)


def message(hwnd, msg, kind, value=0):
    result = ctypes.c_size_t()
    if not u.SendMessageTimeoutW(hwnd, msg, kind, value, 2, 1000,
                                 ctypes.byref(result)):
        raise OSError("Window stopped responding")
    return result.value


def properties(hwnd, desired=None):
    ptr = ctypes.c_void_p()
    hr = shell.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(IID), ctypes.byref(ptr))
    if hr < 0:
        raise OSError("Window property store: 0x%08x" % (hr & 0xffffffff))
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(KEY),
                             ctypes.POINTER(PV))(table[5])
    put = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(KEY),
                             ctypes.POINTER(PV))(table[6])
    release = ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(table[2])
    result = {}
    changed = False
    try:
        for pid in (2, 3, 4, 5):
            key = KEY(FMT, pid)
            val = PV()
            hr = get(ptr, ctypes.byref(key), ctypes.byref(val))
            if hr < 0:
                raise OSError("Read property: 0x%08x" % (hr & 0xffffffff))
            try:
                current = ctypes.wstring_at(val.v.text) if val.vt == 31 and val.v.text else ""
                result[pid] = current
            finally:
                ole.PropVariantClear(ctypes.byref(val))
            if desired is not None and current != desired[pid]:
                buf = ctypes.create_unicode_buffer(desired[pid])
                new = PV()
                new.vt = 31
                new.v.text = ctypes.cast(buf, ctypes.c_void_p).value
                hr = put(ptr, ctypes.byref(key), ctypes.byref(new))
                if hr < 0:
                    raise OSError("Write property: 0x%08x" % (hr & 0xffffffff))
                changed = True
        if changed:
            commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[7])
            hr = commit(ptr)
            if hr < 0:
                raise OSError("Commit: 0x%08x" % (hr & 0xffffffff))
    finally:
        release(ptr)
    return result, changed


def claude_windows():
    found = []

    @ENUM
    def callback(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, name, 256)
        if name.value != "Chrome_WidgetWin_1":
            return True
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if Path(process_path(pid.value)).name.lower() == "claude.exe":
            found.append((hwnd, pid.value))
        return True

    u.EnumWindows(callback, 0)
    return found


def run_once(icons, desired, log_path):
    reports = []
    for hwnd, pid in claude_windows():
        changed = False
        for kind, icon in icons.items():
            if message(hwnd, 0x007F, kind) != icon:  # WM_GETICON
                message(hwnd, 0x0080, kind, icon)  # WM_SETICON
                changed = True
        _, prop_changed = properties(hwnd, desired)
        if changed or prop_changed:
            u.RedrawWindow(hwnd, None, None, 0x401)
            log(log_path, "APPLY hwnd=%s pid=%d taskbar=%s" % (hex(hwnd), pid, prop_changed))
        actual, _ = properties(hwnd)
        verified = actual == desired and all(message(hwnd, 0x007F, kind) == icon
                                            for kind, icon in icons.items())
        reports.append({"hwnd": hex(hwnd), "pid": pid, "verified": verified,
                        "app_id": actual[5], "icon": actual[3]})
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--icon", default=str(BASE / "claude.ico"))
    parser.add_argument("--log", default=str(BASE / LOG_NAME))
    parser.add_argument("--package", default=os.environ.get("CLAUDE_PACKAGE_ID", DEFAULT_PACKAGE_ID))
    args = parser.parse_args()

    icon_path = Path(args.icon)
    log_path = Path(args.log)
    if not icon_path.is_file():
        raise FileNotFoundError(str(icon_path))
    if args.launch:
        os.startfile("shell:AppsFolder\\" + args.package)

    icons = {kind: u.LoadImageW(None, str(icon_path), 1, size, size, 0x8010)
             for kind, size in ((0, 16), (1, 32))}
    if not all(icons.values()):
        raise OSError("Cannot load Claude icon")

    desired = {2: '"%s\\explorer.exe" shell:AppsFolder\\%s' %
                  (os.environ.get("WINDIR", r"C:\Windows"), args.package),
               3: str(icon_path) + ",0", 4: "Claude", 5: APP_ID}

    if args.once:
        print(json.dumps(run_once(icons, desired, log_path), ensure_ascii=False))
        return

    mutex = k.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        k.CloseHandle(mutex)
        return
    log(log_path, "START pid=%d" % os.getpid())
    last_error = ""
    try:
        while True:
            try:
                run_once(icons, desired, log_path)
                last_error = ""
            except OSError as exc:
                if str(exc) != last_error:  # 重复错误只记一次，不反复骚扰 Shell
                    log(log_path, "ERROR " + str(exc))
                    last_error = str(exc)
            time.sleep(5)
    finally:
        k.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(Path(__file__).resolve().parent / LOG_NAME, "FATAL " + repr(exc))
        raise
