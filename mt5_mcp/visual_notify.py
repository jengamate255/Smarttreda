"""On-screen popup notifications for trade events.

Dependency-free (tkinter only): a small topmost toast appears on the user's
desktop when a trade is placed, without stealing focus. Safe to call from any
thread of a background watcher process.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import tkinter as tk

logger = logging.getLogger("mt5_autotrader.visual_notify")

_counter = 0
_lock = threading.Lock()
WS_EX_NOACTIVATE = 0x08000000


def _run(title: str, message: str, seconds: float) -> None:
    global _counter  # noqa: PLW0603
    with _lock:
        _counter += 1
        row = (_counter - 1) % 5

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)

    frame = tk.Frame(root, bg="#1f2430", highlightbackground="#f59e0b", highlightthickness=1)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame,
        text=title,
        bg="#1f2430",
        fg="#f59e0b",
        font=("Segoe UI", 10, "bold"),
        anchor="w",
        padx=12,
        pady=6,
    ).pack(fill="x")
    tk.Label(
        frame,
        text=message,
        bg="#1f2430",
        fg="#e5e7eb",
        font=("Consolas", 10),
        justify="left",
        anchor="w",
        padx=12,
        pady=8,
    ).pack(fill="x")

    root.update_idletasks()
    width = max(frame.winfo_reqwidth(), 360)
    height = frame.winfo_reqheight()
    x = root.winfo_screenwidth() - width - 24
    y = 48 + row * (height + 8)
    root.geometry(f"{width}x{height}+{x}+{y}")

    if sys.platform == "win32":
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongPtrW(hwnd, -20, style | WS_EX_NOACTIVATE)

    root.after(int(seconds * 1000), root.destroy)
    try:
        root.mainloop()
    except tk.TclError:
        pass


def popup(title: str, message: str, seconds: float = 8.0) -> None:
    """Show a toast; never raises and never blocks the caller."""
    try:
        threading.Thread(
            target=_run, args=(title, message, seconds), daemon=True
        ).start()
    except Exception as exc:  # noqa: BLE001 - notifications must never crash the trader
        logger.warning("visual popup failed: %s", exc)