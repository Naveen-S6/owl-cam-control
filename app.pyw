"""Owl Cam Control: a tray app to frame the webcam (zoom/pan/tilt) and tune focus, exposure and color.

Left-click the tray icon to open the panel; right-click for presets, virtual camera, startup and quit.
Ctrl+Alt+O toggles "Send to apps" from anywhere. A pop-up offers to turn it on when a meeting starts,
and it switches itself off after a few minutes without a meeting.
"""
import ctypes
import ctypes.wintypes
import logging
import queue
import sys
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

import comtypes
import customtkinter as ctk
import cv2
import pystray
from PIL import Image, ImageDraw, ImageTk

import camera
import meeting
import pipeline
import store
from ui import (ACCENT, ACCENT_HOVER, BAD, BG, BORDER, CARD, FAINT, FONT, GOOD, MUTED, TEXT, TRACK, WARN, Toast,
                cursor_work_area)

TITLE = "Owl Cam Control"
DEFAULT_SIZE, MIN_SIZE = (1180, 720), (860, 560)  # window, in 96-DPI units
CONTROLS_WIDTH = 360
DEVICE_POLL_MS = 3000
MEETING_POLL_MS = 2000
SAVE_DELAY_MS = 800
HOTKEY_TEXT = "Ctrl+Alt+O"
HOTKEY_MODS, HOTKEY_KEY = 0x0002 | 0x0001 | 0x4000, ord("O")  # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
CAMERA_ERRORS = (comtypes.COMError, OSError)

log = logging.getLogger(TITLE)


def setup_logging():
    """Log to %APPDATA%/OwlCamControl/app.log (rotated at 1 MB, 3 backups)."""
    store.APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(store.APP_DIR / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    sys.excepthook = lambda *exc: log.critical("Unhandled error", exc_info=exc)


def claim_single_instance():
    """Return True if no other copy is running (holds a named mutex for the process lifetime)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    claim_single_instance.handle = kernel32.CreateMutexW(None, False, "Local\\OwlCamControl")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def start_hotkey(callback, on_fail):
    """Register the global hotkey on its own thread; callback()/on_fail() run on that thread."""
    def loop():
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, HOTKEY_MODS, HOTKEY_KEY):
            on_fail()
            return
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                callback()
    threading.Thread(target=loop, daemon=True).start()


def tray_image():
    """Draw the 64x64 tray icon: a simple camera glyph."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 16, 60, 56), radius=10, fill=ACCENT)
    d.rectangle((22, 8, 42, 18), fill=ACCENT)
    d.ellipse((20, 22, 44, 46), fill="white")
    d.ellipse((26, 28, 38, 40), fill=BG)
    return img


def font(size, weight="normal"):
    """Shorthand for the app font at a size/weight."""
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def card(parent, title):
    """Rounded panel with a small uppercase heading; returns (outer, inner content frame)."""
    outer = ctk.CTkFrame(parent, fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=14)
    ctk.CTkLabel(outer, text=title.upper(), font=font(11, "bold"), text_color=MUTED).pack(
        anchor="w", padx=18, pady=(14, 4))
    inner = ctk.CTkFrame(outer, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=18, pady=(0, 14))
    return outer, inner


def slider(parent, lo, hi, steps, command, **kw):
    """CTkSlider styled with the app palette."""
    kw.setdefault("height", 16)
    return ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, command=command,
                         progress_color=ACCENT, button_color=TEXT, button_hover_color="#FFFFFF",
                         fg_color=TRACK, **kw)


def format_value(key, value):
    """Human-readable slider value for a control key."""
    if key == "frame_zoom":
        return f"{value:.2f}×"
    if key == "white_balance":
        return f"{int(value)} K"
    return f"{int(round(value))}"


def fit_16_9(width, height):
    """Largest 16:9 (w, h) that fits inside width x height (at least 16x9)."""
    w = max(16, min(width, height * 16 // 9))
    return w, max(9, w * 9 // 16)


class App:
    """CustomTkinter panel + pystray tray icon. All camera (COM) calls run on the Tk thread."""

    def __init__(self):
        """Load settings, build the UI, connect the camera, start tray, hotkey and meeting watcher."""
        first_run = not store.SETTINGS_FILE.exists()
        self.settings = store.load()
        if first_run:
            self._try(lambda: store.set_startup(True), "Couldn't enable start with Windows")
        self.cam, self.pipe, self._save_job, self._photo, self._drag = None, None, None, None, None
        self._ticking, self._sized, self._shown_size = False, False, (1, 1)
        self.framing = dict(pipeline.DEFAULT_FRAMING)  # shared (same object) with the pipeline thread
        self._load_framing(self.settings["last"])
        self.rows = {}  # hardware key -> (slider, value_label, auto_var or None)
        self.inbox = queue.Queue()  # callables posted from other threads
        self.watcher = meeting.MeetingWatcher(idle_off=self.settings["auto_off_minutes"] * 60)

        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk(fg_color=BG)
        self.root.withdraw()
        self.root.report_callback_exception = self.on_error
        self._build_window()
        self.connect_camera()

        self.tray = pystray.Icon(store.APP_NAME, tray_image(), TITLE, self._tray_menu())
        self.tray.run_detached()
        start_hotkey(lambda: self.post(lambda: self.toggle_vcam(announce=True)),
                     lambda: self.post(self._hotkey_failed))
        self.root.after(100, self._drain_inbox)
        self.root.after(DEVICE_POLL_MS, self._poll_device)
        self.root.after(MEETING_POLL_MS, self._poll_meeting)

    # ---------- generic helpers ----------

    def post(self, fn):
        """Queue fn to run on the Tk thread (safe to call from other threads)."""
        self.inbox.put(fn)

    def _drain_inbox(self):
        """Run callables posted from other threads, then reschedule."""
        while not self.inbox.empty():
            self.inbox.get()()
        self.root.after(100, self._drain_inbox)

    def _try(self, fn, message):
        """Run fn; on failure log it and show message to the user. Returns True on success."""
        try:
            fn()
            return True
        except Exception:
            log.exception(message)
            messagebox.showerror(TITLE, f"{message}. Please try again.")
            return False

    def on_error(self, exc, value, tb):
        """Global Tk callback error handler: log details, show a friendly message."""
        log.error("Unhandled UI error", exc_info=(exc, value, tb))
        messagebox.showerror(TITLE, "Something went wrong. Details were saved to the log file.\n"
                                    "Try again, or restart the app from the Start menu.")

    def set_status(self, text, color):
        """Update the status pill in the header."""
        self.status.configure(text=f"●  {text}", text_color=color)

    def _save_settings(self):
        """Persist settings, telling the user if it fails."""
        self._try(lambda: store.save(self.settings), "Couldn't save settings")

    # ---------- window ----------

    def _build_window(self):
        """Header, then two resizable columns: framing + presets | tabbed camera controls."""
        r = self.root
        r.title(TITLE)
        r.minsize(*MIN_SIZE)
        r.protocol("WM_DELETE_WINDOW", self.hide)
        r.bind("<Escape>", lambda e: self.hide())
        r.bind("<Unmap>", lambda e: self._update_pipeline() if e.widget is r else None)
        r.bind("<Map>", lambda e: self._on_map() if e.widget is r else None)
        self._icon = ImageTk.PhotoImage(tray_image())
        r.after(300, lambda: r.iconphoto(True, self._icon))  # CTk sets its own icon first
        r.grid_columnconfigure(0, weight=1)
        r.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(r, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(20, 12))
        ctk.CTkLabel(header, text="Owl Cam", font=font(22, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(header, text="  Studio controls", font=font(14), text_color=MUTED).pack(side="left", pady=(4, 0))
        ctk.CTkLabel(header, text=HOTKEY_TEXT, font=font(11, "bold"), text_color=MUTED, fg_color=TRACK,
                     corner_radius=6, padx=6).pack(side="right", padx=(10, 0))
        self.vcam_var = tk.BooleanVar(value=self.settings["virtual_camera"])
        ctk.CTkSwitch(header, text="Send to apps", variable=self.vcam_var, command=self.on_vcam_switch,
                      font=font(13), text_color=TEXT, progress_color=ACCENT, button_color=TEXT,
                      fg_color=TRACK).pack(side="right")
        self.status = ctk.CTkLabel(header, text="", font=font(12, "bold"), fg_color=CARD, corner_radius=12,
                                   height=28, padx=12)
        self.status.pack(side="right", padx=16)

        left = ctk.CTkFrame(r, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(24, 14), pady=(0, 24))
        self._build_preview_card(left)
        self._build_presets_card(left)

        outer, inner = card(r, "Camera")
        outer.configure(width=CONTROLS_WIDTH)
        outer.grid(row=1, column=1, sticky="ns", padx=(0, 24), pady=(0, 24))
        tabs = ctk.CTkTabview(inner, width=CONTROLS_WIDTH - 40, fg_color="transparent",
                              segmented_button_fg_color=TRACK, segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER,
                              segmented_button_unselected_color=TRACK,
                              segmented_button_unselected_hover_color=BORDER, text_color=TEXT)
        tabs.pack(fill="both", expand=True)
        self.hw_cards = {group: tabs.add(group) for group in ("Focus & light", "Color & detail")}

    def _build_preview_card(self, parent):
        """Live preview (grows with the window) with drag-to-pan / scroll-to-zoom and framing sliders."""
        outer, inner = card(parent, "Framing")
        outer.pack(fill="both", expand=True)
        view = ctk.CTkFrame(inner, fg_color="transparent")
        view.pack(fill="both", expand=True)
        lo, hi = pipeline.OFFSET_RANGE
        self.tilt = slider(view, hi, lo, hi - lo, lambda v: self.on_frame("frame_y", v),
                           orientation="vertical", width=16)
        self.tilt.pack(side="right", fill="y", padx=(12, 0))
        self.preview = tk.Label(view, bg="#000000", fg=MUTED, font=(FONT, 11), wraplength=420,
                                justify="center", cursor="fleur", bd=0)
        self.preview.pack(side="left", fill="both", expand=True)
        self.preview.bind("<ButtonPress-1>", self._drag_start)
        self.preview.bind("<B1-Motion>", self._drag_move)
        self.preview.bind("<ButtonRelease-1>", lambda e: self._schedule_save())
        self.preview.bind("<MouseWheel>", self._wheel_zoom)

        self.pan = slider(inner, lo, hi, hi - lo, lambda v: self.on_frame("frame_x", v))
        self.pan.pack(fill="x", pady=(12, 6), padx=(0, 28))
        zrow = ctk.CTkFrame(inner, fg_color="transparent")
        zrow.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(zrow, text="Zoom", font=font(13), text_color=TEXT).pack(side="left")
        zlo, zhi = pipeline.ZOOM_RANGE
        self.zoom = slider(zrow, zlo, zhi, int((zhi - zlo) * 20), lambda v: self.on_frame("frame_zoom", v))
        self.zoom.pack(side="left", fill="x", expand=True, padx=12)
        self.zoom_label = ctk.CTkLabel(zrow, text="", width=52, font=font(13, "bold"), text_color=TEXT)
        self.zoom_label.pack(side="left")
        ctk.CTkButton(zrow, text="Center", width=70, height=28, corner_radius=8, fg_color=TRACK,
                      hover_color=BORDER, text_color=TEXT, font=font(12),
                      command=self.center_framing).pack(side="left", padx=(8, 0))
        self.hint = ctk.CTkLabel(inner, text="", font=font(12), text_color=FAINT, justify="left", anchor="w")
        self.hint.pack(fill="x", pady=(10, 0))
        self._sync_framing_ui()

    def _build_presets_card(self, parent):
        """Preset picker with save/delete and a reset-everything button."""
        outer, inner = card(parent, "Presets")
        outer.pack(fill="x", pady=(14, 0))
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        self.preset_var = tk.StringVar()
        self.preset_menu = ctk.CTkOptionMenu(row, variable=self.preset_var, values=[""], width=190, height=32,
                                             corner_radius=8, fg_color=TRACK, button_color=TRACK,
                                             button_hover_color=BORDER, dropdown_fg_color=CARD,
                                             font=font(13), command=self.apply_preset)
        self.preset_menu.pack(side="left")
        for text, cmd, primary in (("Save as…", self.save_preset, True), ("Delete", self.delete_preset, False)):
            ctk.CTkButton(row, text=text, width=84, height=32, corner_radius=8, font=font(13),
                          fg_color=ACCENT if primary else TRACK, hover_color=ACCENT_HOVER if primary else BORDER,
                          text_color=TEXT, command=cmd).pack(side="left", padx=(10, 0))
        ctk.CTkButton(row, text="Reset all", width=84, height=32, corner_radius=8, font=font(13),
                      fg_color="transparent", border_width=1, border_color=BORDER, hover_color=TRACK,
                      text_color=MUTED, command=self.reset).pack(side="right")
        self.default_var = tk.BooleanVar()
        self.default_switch = ctk.CTkSwitch(
            inner, text="Always use this preset — at startup, reconnect and when sending starts",
            variable=self.default_var, command=self.on_default_switch, font=font(12), text_color=MUTED,
            progress_color=ACCENT, button_color=TEXT, fg_color=TRACK, switch_width=30, switch_height=16)
        self.default_switch.pack(anchor="w", pady=(12, 0))
        self._refresh_presets()

    def _build_controls(self):
        """(Re)create a row for every hardware control the connected camera supports."""
        for inner in self.hw_cards.values():
            for child in inner.winfo_children():
                child.destroy()
        self.rows.clear()
        for key, (label, group, _, _) in camera.CONTROLS.items():
            if key in self.cam.ranges:
                self._add_control_row(self.hw_cards[group], key, label, self.cam.ranges[key])

    def _add_control_row(self, parent, key, label, rng):
        """Label + value (+ Auto switch) on one line, full-width slider underneath."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(8, 4))
        ctk.CTkLabel(row, text=label, font=font(13), text_color=TEXT).pack(side="left")
        auto_var = None
        if rng.can_auto:
            auto_var = tk.BooleanVar()
            ctk.CTkSwitch(row, text="Auto", variable=auto_var, width=60, switch_width=30, switch_height=16,
                          font=font(12), text_color=MUTED, progress_color=ACCENT, button_color=TEXT,
                          fg_color=TRACK, command=lambda: self.on_auto(key)).pack(side="right")
        value = ctk.CTkLabel(row, text="", font=font(13, "bold"), text_color=TEXT, width=56, anchor="e")
        value.pack(side="right", padx=(0, 10))
        s = slider(parent, rng.min, rng.max, max(1, (rng.max - rng.min) // rng.step),
                   lambda v: self.on_slide(key, v))
        s.pack(fill="x", pady=(0, 6))
        self.rows[key] = (s, value, auto_var)

    def show(self):
        """Open (or restore) the panel on the monitor under the mouse and start the live preview."""
        r = self.root
        if r.state() == "iconic":
            r.deiconify()
        elif not self._visible():
            r.deiconify()
            r.update_idletasks()
            self._place_window()
        r.lift()
        r.focus_force()
        self._on_map()

    def _place_window(self):
        """Size the window to fit the monitor (first time only) and put it bottom-right above the taskbar."""
        r = self.root
        work = cursor_work_area()
        scale = ctk.ScalingTracker.get_window_scaling(r)  # CTk geometry() takes 96-DPI units
        avail_w, avail_h = (work.right - work.left) / scale - 32, (work.bottom - work.top) / scale - 64
        if not self._sized:
            w, h = min(DEFAULT_SIZE[0], avail_w), min(DEFAULT_SIZE[1], avail_h)
            r.geometry(f"{int(max(w, MIN_SIZE[0]))}x{int(max(h, MIN_SIZE[1]))}")
            r.update_idletasks()
            self._sized = True
        x = max(work.right - r.winfo_width() - 16, work.left) / scale
        y = max(work.bottom - r.winfo_height() - 48, work.top) / scale
        r.geometry(f"+{int(x)}+{int(y)}")

    def _on_map(self):
        """Window shown/restored: make sure the pipeline and preview loop are running."""
        self._update_pipeline()
        if not self._ticking and self._visible():
            self._ticking = True
            self._preview_tick()

    def hide(self):
        """Hide the panel to the tray (the pipeline keeps running only if apps are being fed)."""
        self.root.withdraw()
        self._update_pipeline()

    def _visible(self):
        """True while the panel is on screen (not hidden to tray and not minimized)."""
        return self.root.state() == "normal" or self.root.state() == "zoomed"

    # ---------- pipeline & preview ----------

    def _update_pipeline(self):
        """Run the capture pipeline while a camera is connected and either apps or the panel need it."""
        want = self.cam is not None and (self.settings["virtual_camera"] or self._visible())
        if self.pipe and (not want or self.pipe.state == "unavailable"):
            self.pipe.stop()
            self.pipe = None
        if want and not self.pipe:
            self.pipe = pipeline.Pipeline(self.cam.index, self.framing, self.settings["virtual_camera"])
        if self.pipe:
            self.pipe.publish = self.settings["virtual_camera"]
        self._refresh_status()

    def _refresh_status(self):
        """Set the status pill and hint from camera / pipeline / virtual-camera state."""
        vcam = self.settings["virtual_camera"]
        if not self.cam:
            self.set_status("Camera not connected", WARN)
        elif self.pipe and self.pipe.state == "unavailable":
            self.set_status("Camera busy in another app", WARN)
        elif self.pipe and self.pipe.state == "starting":
            self.set_status("Starting…", MUTED)
        elif self.pipe and self.pipe.vcam_error:
            self.set_status("Virtual camera missing — install OBS Studio", BAD)
        else:
            self.set_status(f"{'Sending to apps' if vcam else 'Preview only'} · {self.cam.name}", GOOD)
        self.hint.configure(text=("Drag the picture to move it · scroll to zoom\n" +
                                  ("In Zoom / Teams / Meet, choose the camera “OBS Virtual Camera”." if vcam else
                                   f"Turn on “Send to apps” ({HOTKEY_TEXT}) to use this framing in your meeting.")))

    def _preview_tick(self):
        """Copy the newest pipeline frame into the panel (~25 fps) while it is on screen."""
        if not self._visible():
            self._ticking = False
            return
        p = self.pipe
        if p and p.frame is not None and p.state == "live":
            self._shown_size = fit_16_9(self.preview.winfo_width(), self.preview.winfo_height())
            rgb = cv2.cvtColor(cv2.resize(p.frame, self._shown_size, interpolation=cv2.INTER_AREA),
                               cv2.COLOR_BGR2RGB)
            self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
            self.preview.configure(image=self._photo, text="")
        else:
            self.preview.configure(image="", text=self._preview_message())
        self._refresh_status()
        self.root.after(40, self._preview_tick)

    def _preview_message(self):
        """Text shown in the preview area when there is no picture."""
        if not self.cam:
            return f"Plug in the camera named “{self.settings['camera_name']}”."
        if self.pipe and self.pipe.state == "unavailable":
            return ("The camera is open in another app.\nIn that app, switch the camera to “OBS Virtual Camera” "
                    "so it gets the framed picture, then the preview returns here.")
        return "Starting camera…"

    # ---------- framing ----------

    def _load_framing(self, settings):
        """Copy frame_* values from a settings dict into self.framing (in place, clamped)."""
        for key in pipeline.DEFAULT_FRAMING:
            if key in settings:
                lo, hi = pipeline.ZOOM_RANGE if key == "frame_zoom" else pipeline.OFFSET_RANGE
                self.framing[key] = pipeline.clamp(float(settings[key]["value"]), lo, hi)

    def _sync_framing_ui(self):
        """Move framing sliders to self.framing."""
        self.zoom.set(self.framing["frame_zoom"])
        self.pan.set(self.framing["frame_x"])
        self.tilt.set(self.framing["frame_y"])
        self.zoom_label.configure(text=format_value("frame_zoom", self.framing["frame_zoom"]))

    def on_frame(self, key, value):
        """Framing slider moved: update the shared framing (the pipeline picks it up next frame)."""
        self.framing[key] = float(value)
        self.zoom_label.configure(text=format_value("frame_zoom", self.framing["frame_zoom"]))
        self._schedule_save()

    def _drag_start(self, event):
        """Remember where a drag on the preview began."""
        self._drag = (event.x, event.y, self.framing["frame_x"], self.framing["frame_y"])

    def _drag_move(self, event):
        """Move the crop with the mouse so the picture follows the cursor."""
        zoom = self.framing["frame_zoom"]
        if not self._drag or zoom <= 1.0:
            return
        x0, y0, fx, fy = self._drag
        k = 200 / (zoom - 1)  # displayed-picture pixels -> offset percent at this zoom
        lo, hi = pipeline.OFFSET_RANGE
        self.framing["frame_x"] = pipeline.clamp(fx - (event.x - x0) / self._shown_size[0] * k, lo, hi)
        self.framing["frame_y"] = pipeline.clamp(fy - (event.y - y0) / self._shown_size[1] * k, lo, hi)
        self._sync_framing_ui()

    def _wheel_zoom(self, event):
        """Mouse wheel over the preview zooms in/out in 0.1x steps."""
        lo, hi = pipeline.ZOOM_RANGE
        self.framing["frame_zoom"] = pipeline.clamp(self.framing["frame_zoom"] + 0.1 * event.delta / 120, lo, hi)
        self._sync_framing_ui()
        self._schedule_save()

    def center_framing(self):
        """Re-center the crop without changing zoom."""
        self.framing.update(frame_x=0.0, frame_y=0.0)
        self._sync_framing_ui()
        self._schedule_save()

    # ---------- camera ----------

    def connect_camera(self):
        """Find the camera, reapply the last settings, and build its sliders."""
        try:
            self.cam = camera.find_camera(self.settings["camera_name"])
        except CAMERA_ERRORS:
            log.exception("Camera lookup failed")
            self.cam = None
        if self.cam:
            log.info("Connected to %s", self.cam.name)
            if self._camera_call(lambda: self.cam.apply(self.settings["last"])):
                self._build_controls()
                self._sync_from_camera()
                self.apply_default_preset()
        self._update_pipeline()

    def _camera_call(self, fn):
        """Run a camera operation; if it fails the camera was likely unplugged. Returns success."""
        try:
            fn()
            return True
        except CAMERA_ERRORS:
            log.warning("Camera call failed; treating camera as disconnected", exc_info=True)
            self.on_camera_lost()
            return False

    def on_camera_lost(self):
        """Drop the camera and wait for it to come back (the poll reconnects it)."""
        self.cam = None
        for inner in self.hw_cards.values():
            for child in inner.winfo_children():
                child.destroy()
        self.rows.clear()
        self._update_pipeline()

    def _poll_device(self):
        """Every few seconds: reconnect a returning camera, notice an unplugged one, retry a busy one."""
        try:
            names = [name for name, _ in camera.list_video_devices()]
        except CAMERA_ERRORS:
            log.exception("Device enumeration failed")
            names = None
        if names is not None:
            if self.cam and self.cam.name not in names:
                self.on_camera_lost()
            elif not self.cam:
                self.connect_camera()
            else:
                self._update_pipeline()
        self.root.after(DEVICE_POLL_MS, self._poll_device)

    def _sync_from_camera(self):
        """Move hardware sliders/switches to the camera's current values."""
        for key, (s, value, auto_var) in self.rows.items():
            state = self.cam.get(key)
            s.set(state["value"])
            value.configure(text=format_value(key, state["value"]))
            if auto_var is not None:
                auto_var.set(state["auto"])
                s.configure(state="disabled" if state["auto"] else "normal",
                            progress_color=FAINT if state["auto"] else ACCENT)

    def on_slide(self, key, v):
        """Hardware slider moved: write the value to the camera and schedule a save."""
        if not self.cam:
            return
        _, value, auto_var = self.rows[key]
        value.configure(text=format_value(key, v))
        auto = bool(auto_var and auto_var.get())
        if self._camera_call(lambda: self.cam.set(key, round(v), auto)):
            self._schedule_save()

    def on_auto(self, key):
        """Auto toggled: when going manual, start from the value auto mode picked."""
        if not self.cam:
            return
        s, _, auto_var = self.rows[key]
        auto = auto_var.get()

        def apply():
            if not auto:
                s.set(self.cam.get(key)["value"])
            self.cam.set(key, round(s.get()), auto)

        if self._camera_call(apply):
            self._sync_from_camera()
            self._schedule_save()

    def _current_settings(self):
        """Camera hardware snapshot plus software framing, as one settings dict."""
        data = self.cam.snapshot() if self.cam else dict(self.settings["last"])
        data.update({k: {"value": v} for k, v in self.framing.items()})
        return data

    def _schedule_save(self):
        """Save the current settings as 'last' after a short pause (debounced)."""
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(SAVE_DELAY_MS, self._save_last)

    def _save_last(self):
        """Persist current settings so they are restored at login / replug."""
        self._save_job = None
        if self._camera_call(lambda: self.settings.update(last=self._current_settings())):
            self._save_settings()

    def reset(self):
        """Restore the camera's factory defaults and full-frame framing."""
        self.framing.update(pipeline.DEFAULT_FRAMING)
        self._sync_framing_ui()
        if self.cam and self._camera_call(self.cam.reset):
            self._sync_from_camera()
        self._schedule_save()

    # ---------- send to apps (virtual camera) ----------

    def set_vcam(self, on):
        """Turn 'Send to apps' on/off everywhere (switch, tray, pipeline, settings). On applies the default preset."""
        if on:
            self.apply_default_preset()
        self.settings["virtual_camera"] = on
        self.vcam_var.set(on)
        self._save_settings()
        self._update_pipeline()
        self.tray.update_menu()
        log.info("Send to apps %s", "on" if on else "off")

    def on_vcam_switch(self):
        """Header switch changed."""
        self.set_vcam(self.vcam_var.get())

    def toggle_vcam(self, announce=False):
        """Flip 'Send to apps' (tray menu / hotkey). announce: show a short pop-up with the new state."""
        self.set_vcam(not self.settings["virtual_camera"])
        if announce:
            on = self.settings["virtual_camera"]
            Toast(self.root, f"Send to apps is {'ON' if on else 'OFF'}",
                  "Your framed camera is live as “OBS Virtual Camera”." if on else
                  "The camera is released and no longer uses CPU.", timeout_ms=3000)

    def _hotkey_failed(self):
        """Another app owns the shortcut: log it and say so in the header."""
        log.warning("Global hotkey %s is already used by another app", HOTKEY_TEXT)
        Toast(self.root, f"Shortcut {HOTKEY_TEXT} unavailable",
              "Another app is already using it. Use the tray menu or the panel switch instead.", timeout_ms=6000)

    # ---------- meeting detection ----------

    def _poll_meeting(self):
        """Every 2 s: check which apps use the camera/mic; offer 'Send to apps' or auto-switch it off."""
        try:
            apps = meeting.apps_in_use(self.settings["meeting_ignore_apps"])
        except OSError:
            log.exception("Reading camera/mic usage failed")
            apps = []
        for event in self.watcher.update(apps, time.monotonic(), self.settings["virtual_camera"]):
            if event[0] == "meeting" and not self.settings["virtual_camera"] and self.settings["meeting_popups"]:
                self._meeting_popup(event[1])
            elif event[0] == "idle_off" and self.settings["virtual_camera"]:
                self._auto_off()
        self.root.after(MEETING_POLL_MS, self._poll_meeting)

    def _meeting_popup(self, apps):
        """Pop-up: a meeting app started using the camera/mic — offer to send the framed camera."""
        log.info("Meeting detected: %s", ", ".join(apps))
        Toast(self.root, "Looks like a meeting is starting",
              f"{' & '.join(apps[:2])} is using your camera or microphone. "
              f"Send your framed Owl camera to it? ({HOTKEY_TEXT})",
              actions=[("Send to apps", lambda: self.set_vcam(True), True), ("Not now", lambda: None, False)],
              timeout_ms=20000)

    def _auto_off(self):
        """No meeting for a while: switch 'Send to apps' off to free the camera and CPU."""
        minutes = self.settings["auto_off_minutes"]
        self.set_vcam(False)
        Toast(self.root, "Send to apps switched off",
              f"No meeting for {minutes} minutes, so the camera was released to save CPU.",
              actions=[("Turn back on", lambda: self.set_vcam(True), True)], timeout_ms=10000)

    def toggle_meeting_popups(self):
        """Tray toggle for the meeting pop-up."""
        self.settings["meeting_popups"] = not self.settings["meeting_popups"]
        self._save_settings()
        self.tray.update_menu()

    # ---------- presets ----------

    def _refresh_presets(self):
        """Update the preset dropdown (and tray menu) from settings."""
        names = sorted(self.settings["presets"])
        self.preset_menu.configure(values=names or ["No presets yet"])
        self.preset_var.set(self.settings["active_preset"] or ("Choose a preset" if names else "No presets yet"))
        selected = self.preset_var.get() in self.settings["presets"]
        self.default_var.set(selected and self.preset_var.get() == self.settings["default_preset"])
        self.default_switch.configure(state="normal" if selected else "disabled")
        if hasattr(self, "tray"):
            self.tray.update_menu()

    def apply_default_preset(self):
        """Apply the 'always use' preset, if one is set and still exists."""
        if self.settings["default_preset"] in self.settings["presets"]:
            self.apply_preset(self.settings["default_preset"])

    def on_default_switch(self):
        """Make the selected preset the default (or clear the default)."""
        name = self.preset_var.get()
        if name not in self.settings["presets"]:
            self.default_var.set(False)
            return
        self.settings["default_preset"] = name if self.default_var.get() else None
        self._save_settings()
        self._refresh_presets()

    def apply_preset(self, name):
        """Apply a saved preset (hardware settings + framing)."""
        preset = self.settings["presets"].get(name)
        if preset is None:
            return
        if self.cam and not self._camera_call(lambda: self.cam.apply(preset)):
            return
        self._load_framing(preset)
        self._sync_framing_ui()
        if self.cam:
            self._sync_from_camera()
        self.settings["active_preset"] = name
        self._save_last()
        self._refresh_presets()

    def save_preset(self):
        """Ask for a name and save the current settings as a preset."""
        dialog = ctk.CTkInputDialog(title=TITLE, text="Name this preset (e.g. Meeting):", fg_color=CARD,
                                    button_fg_color=ACCENT, button_hover_color=ACCENT_HOVER)
        name = dialog.get_input()
        if name is None:
            return
        try:
            name = store.clean_preset_name(name)
        except ValueError as e:
            messagebox.showwarning(TITLE, str(e))
            return
        if name in self.settings["presets"] and not messagebox.askyesno(TITLE, f"Replace preset “{name}”?"):
            return
        if self._camera_call(lambda: self.settings["presets"].update({name: self._current_settings()})):
            self.settings["active_preset"] = name
            self._save_settings()
            self._refresh_presets()

    def delete_preset(self):
        """Delete the selected preset, after confirming."""
        name = self.preset_var.get()
        if name not in self.settings["presets"] or not messagebox.askyesno(TITLE, f"Delete preset “{name}”?"):
            return
        self.settings["presets"].pop(name)
        for key in ("active_preset", "default_preset"):
            if self.settings[key] == name:
                self.settings[key] = None
        self._save_settings()
        self._refresh_presets()

    # ---------- tray ----------

    def _tray_menu(self):
        """Right-click menu. Left-click runs the default item (open the panel)."""
        return pystray.Menu(
            pystray.MenuItem("Open controls", lambda: self.post(self.show), default=True),
            pystray.MenuItem(f"Send to apps\t{HOTKEY_TEXT}", lambda: self.post(self.toggle_vcam),
                             checked=lambda item: self.settings["virtual_camera"]),
            pystray.MenuItem("Presets", pystray.Menu(self._preset_items)),
            pystray.MenuItem("Reset all", lambda: self.post(self.reset)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Meeting pop-ups", lambda: self.post(self.toggle_meeting_popups),
                             checked=lambda item: self.settings["meeting_popups"]),
            pystray.MenuItem("Start with Windows", lambda: self.post(self.toggle_startup),
                             checked=lambda item: store.startup_enabled()),
            pystray.MenuItem("Quit", lambda: self.post(self.quit)),
        )

    def _preset_items(self):
        """Build the Presets submenu (one radio item per saved preset)."""
        names = sorted(self.settings["presets"])
        if not names:
            yield pystray.MenuItem("No presets yet — save one in the panel", None, enabled=False)
        for name in names:
            label = f"{name}  ★ default" if name == self.settings["default_preset"] else name
            yield pystray.MenuItem(label, self._preset_action(name),
                                   checked=lambda _it, n=name: self.settings["active_preset"] == n, radio=True)

    def _preset_action(self, name):
        """Zero-arg tray callback that applies preset name (pystray rejects lambdas with default args)."""
        return lambda: self.post(lambda: self.apply_preset(name))

    def toggle_startup(self):
        """Flip the 'start with Windows' registry entry."""
        self._try(lambda: store.set_startup(not store.startup_enabled()), "Couldn't change the startup setting")
        self.tray.update_menu()

    def quit(self):
        """Save, release the camera and exit."""
        if self._save_job:
            self.root.after_cancel(self._save_job)
            self._save_last()
        if self.pipe:
            self.pipe.stop()
        self.tray.stop()
        self.root.destroy()

    def run(self):
        """Enter the Tk event loop."""
        self.root.mainloop()


if __name__ == "__main__":
    setup_logging()
    if not claim_single_instance():
        sys.exit(0)
    App().run()
