"""Shared UI bits: the dark palette, monitor work-area lookup, and the pop-up Toast."""
import ctypes
import ctypes.wintypes
import tkinter as tk

import customtkinter as ctk

BG, CARD, BORDER = "#0D0F13", "#161920", "#242833"
TEXT, MUTED, FAINT = "#ECEEF3", "#8C93A5", "#5B6170"
ACCENT, ACCENT_HOVER, TRACK = "#5B8CFF", "#4775E8", "#2A2F3B"
GOOD, WARN, BAD = "#3DDC97", "#F5A524", "#FF6B6B"
FONT = "Segoe UI"
WIDTH = 380


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.wintypes.DWORD)]


def cursor_work_area():
    """Work area (screen minus taskbar, physical px) of the monitor under the mouse."""
    user32 = ctypes.windll.user32
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    monitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    return info.rcWork


class Toast:
    """Always-on-top pop-up, bottom-right above the taskbar. Never takes keyboard focus.

    actions: [(label, callback, primary_bool)]. Closes on any action or after timeout_ms.
    """

    current = None  # only one toast on screen at a time

    def __init__(self, root, title, message, actions=(), timeout_ms=8000):
        """root: the app's Tk root (may be hidden)."""
        if Toast.current:
            Toast.current.close()
        Toast.current = self
        self.win = tk.Toplevel(root, bg=BORDER)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        body = ctk.CTkFrame(self.win, fg_color=CARD, corner_radius=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)  # 1 px BORDER outline

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(top, text="●  Owl Cam", font=(FONT, 11, "bold"), text_color=ACCENT).pack(side="left")
        ctk.CTkButton(top, text="✕", width=24, height=24, corner_radius=6, fg_color="transparent",
                      hover_color=TRACK, text_color=MUTED, command=self.close).pack(side="right")
        ctk.CTkLabel(body, text=title, font=(FONT, 15, "bold"), text_color=TEXT, anchor="w",
                     wraplength=WIDTH - 32, justify="left").pack(fill="x", padx=16, pady=(4, 0))
        ctk.CTkLabel(body, text=message, font=(FONT, 12), text_color=MUTED, anchor="w",
                     wraplength=WIDTH - 32, justify="left").pack(fill="x", padx=16, pady=(2, 12))
        if actions:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 14))
            for label, callback, primary in actions:
                ctk.CTkButton(row, text=label, height=32, corner_radius=8, font=(FONT, 13),
                              fg_color=ACCENT if primary else TRACK,
                              hover_color=ACCENT_HOVER if primary else BORDER, text_color=TEXT,
                              command=lambda cb=callback: (self.close(), cb())).pack(
                    side="left", expand=True, fill="x", padx=(0, 8) if primary else 0)
        self._place()
        self._no_activate()
        self._timer = self.win.after(timeout_ms, self.close)

    def _place(self):
        """Bottom-right of the work area on the monitor under the mouse."""
        self.win.update_idletasks()
        work = cursor_work_area()
        scale = ctk.ScalingTracker.get_window_scaling(self.win.master)
        w, h = int(WIDTH * scale), self.win.winfo_reqheight()
        self.win.geometry(f"{w}x{h}+{work.right - w - 16}+{work.bottom - h - 16}")

    def _no_activate(self):
        """Make the pop-up a non-activating tool window (no focus steal, no taskbar button)."""
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.win.winfo_id())
        style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x80)  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

    def close(self):
        """Remove the pop-up (safe to call twice)."""
        if Toast.current is self:
            Toast.current = None
        try:
            self.win.after_cancel(self._timer)
            self.win.destroy()
        except (tk.TclError, AttributeError):
            pass

