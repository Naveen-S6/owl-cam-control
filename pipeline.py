"""Software framing: read the real camera, crop/zoom it, and publish to OBS Virtual Camera.

The Owl Lite reports hardware zoom/pan/tilt but its firmware ignores them, so framing is done here.
Apps (Zoom, Teams, Meet...) must select "OBS Virtual Camera" to see the framed picture.
"""
import logging
import threading

import cv2
import pyvirtualcam

CAPTURE_SIZE = (1920, 1080)
OUTPUT_SIZE = (1280, 720)
FPS = 30
ZOOM_RANGE = (1.0, 4.0)
OFFSET_RANGE = (-100, 100)  # percent of the room the crop has to move in
DEFAULT_FRAMING = {"frame_zoom": 1.0, "frame_x": 0.0, "frame_y": 0.0}

log = logging.getLogger(__name__)


def clamp(value, lo, hi):
    """Return value limited to [lo, hi]."""
    return max(lo, min(hi, value))


def crop_rect(src_w, src_h, zoom, x, y, aspect):
    """Return (left, top, width, height) of the crop for a frame.

    zoom: 1.0 = full frame, 2.0 = half width/height. x, y: -100 (left/top) .. 100 (right/bottom).
    aspect: output width / height; the crop always keeps it so the picture isn't stretched.
    """
    zoom = clamp(zoom, *ZOOM_RANGE)
    x, y = clamp(x, *OFFSET_RANGE), clamp(y, *OFFSET_RANGE)
    base_w, base_h = (src_h * aspect, src_h) if src_w / src_h > aspect else (src_w, src_w / aspect)
    w, h = base_w / zoom, base_h / zoom
    left = (src_w - w) / 2 * (1 + x / 100)
    top = (src_h - h) / 2 * (1 + y / 100)
    return round(left), round(top), round(w), round(h)


def apply_framing(frame, framing, out_size=OUTPUT_SIZE):
    """Crop frame (BGR ndarray) per framing {"frame_zoom","frame_x","frame_y"} and resize to out_size."""
    h, w = frame.shape[:2]
    left, top, cw, ch = crop_rect(w, h, framing["frame_zoom"], framing["frame_x"], framing["frame_y"],
                                  out_size[0] / out_size[1])
    return cv2.resize(frame[top:top + ch, left:left + cw], out_size, interpolation=cv2.INTER_LINEAR)


class Pipeline:
    """Background thread: camera -> framing -> (optional) virtual camera. Latest output kept for preview."""

    def __init__(self, device_index, framing, publish):
        """device_index: DirectShow index; framing: dict shared with the UI; publish: send to virtual cam."""
        self.device_index, self.framing, self.publish = device_index, framing, publish
        self.frame, self.state, self.vcam_error, self.running = None, "starting", None, True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _open_capture(self):
        """Open the camera at full resolution (MJPEG keeps 1080p at 30 fps).

        Format is passed at open time: setting it afterwards renegotiates per property (~3-6 s vs ~1 s).
        """
        return cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW, [
            cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"),
            cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_SIZE[0],
            cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_SIZE[1],
        ])

    def _loop(self):
        """Read frames until stopped or the camera fails; the camera's frame rate paces the loop."""
        cap, vcam = self._open_capture(), None
        self.state = "live" if cap.isOpened() else "unavailable"
        try:
            while self.running and self.state == "live":
                ok, raw = cap.read()
                if not ok:
                    self.state = "unavailable"
                    break
                out = apply_framing(raw, self.framing)
                self.frame = out
                vcam = self._publish(vcam, out)
        finally:
            cap.release()
            if vcam:
                vcam.close()

    def _publish(self, vcam, frame):
        """Send frame to the virtual camera when publishing is on; open/close it as needed."""
        if not self.publish:
            if vcam:
                vcam.close()
            return None
        if vcam is None and self.vcam_error is None:
            try:
                vcam = pyvirtualcam.Camera(*OUTPUT_SIZE, FPS, fmt=pyvirtualcam.PixelFormat.BGR, backend="obs")
            except RuntimeError as e:
                log.error("Virtual camera unavailable: %s", e)
                self.vcam_error = str(e)
                return None
        if vcam:
            vcam.send(frame)
        return vcam

    def stop(self):
        """Stop the thread and release the camera and virtual camera."""
        self.running = False
        self.thread.join(timeout=3)
