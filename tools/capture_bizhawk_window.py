#!/usr/bin/env python3
"""Capture the visible BizHawk game window during scripted playback."""

from __future__ import annotations

import ctypes
import argparse
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "bizhawk"
OUT.mkdir(parents=True, exist_ok=True)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

for function in (
    user32.GetWindowDC,
    gdi32.CreateCompatibleDC,
    gdi32.CreateCompatibleBitmap,
    gdi32.SelectObject,
):
    function.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = [wintypes.HWND]
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BitmapInfoHeader),
        ("bmiColors", wintypes.DWORD * 3),
    ]


gdi32.GetDIBits.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BitmapInfo),
    wintypes.UINT,
]


def capture_window(hwnd: int, path: Path, width: int, height: int) -> None:
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            raise RuntimeError("PrintWindow failed")
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            0,
        ):
            raise RuntimeError("GetDIBits failed")
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
        image.save(path)
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def send_key(hwnd: int, virtual_key: int) -> None:
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, 0x0100, virtual_key, 1)
    user32.keybd_event(virtual_key, 0, 0, 0)
    time.sleep(0.08)
    user32.keybd_event(virtual_key, 0, 2, 0)
    user32.PostMessageW(hwnd, 0x0101, virtual_key, 0xC0000001)


def click_window(hwnd: int, left: int, top: int, x: int, y: int) -> None:
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    user32.SetCursorPos(left + x, top + y)
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def find_window(pid: int | None = None) -> tuple[int, str] | None:
    preferred: list[tuple[int, str]] = []
    fallback: list[tuple[int, str]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if pid is not None and window_pid.value != pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, 256)
        if "BizHawk" in title or "EmuHawk" in title:
            preferred.append((hwnd, title))
        elif "monoeye" in title.lower() and class_buffer.value != "CabinetWClass":
            fallback.append((hwnd, title))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    candidates = preferred or fallback
    return candidates[0] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int)
    parser.add_argument("--play", action="store_true")
    parser.add_argument(
        "--menu-only",
        action="store_true",
        help="Press Start twice and stop on the initial menu without pressing A",
    )
    parser.add_argument("--count", type=int, default=24)
    args = parser.parse_args()
    for _ in range(30):
        target = find_window(args.pid)
        if target:
            break
        time.sleep(0.5)
    else:
        raise SystemExit("BizHawk window not found")

    hwnd, title = target
    print(f"Capturing: {title}")
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    rect = wintypes.RECT()
    for index in range(args.count):
        if (args.play or args.menu_only) and index in (6, 8):
            send_key(hwnd, 0x0D)
        elif args.play and index >= 11 and index % 2 == 1:
            send_key(hwnd, 0x58)
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            break
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if (args.play or args.menu_only) and index == 2:
            click_window(hwnd, rect.left, rect.top, 398, height - 18)
        capture_window(hwnd, OUT / f"window_{index:02d}.png", width, height)
        time.sleep(1)


if __name__ == "__main__":
    main()
