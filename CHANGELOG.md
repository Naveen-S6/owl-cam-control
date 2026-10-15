# Changelog

## 2026-10-15 (license)
- Added an MIT license so others can use, modify and share the code in this public repo.

## 2026-10-15 (docs)
- Rewrote README as a product page for publishing: features, the Kreo Owl Lite Amazon link, the media stream architecture (Mermaid diagram), meeting detection, settings, performance and project layout. Added `docs/meeting-popup.png`.

## 2026-10-15 (default preset)
- **Default preset**: a new "Always use this preset" switch in the Presets card. The chosen preset is applied at startup, on camera reconnect, and whenever Send to apps turns on, so meetings always start with the same look. It's marked ★ in the tray menu and cleared if that preset is deleted.

## 2026-10-15 (meetings)
- **Meeting pop-up**: when another app uses the camera or mic for 8 s, a non-focus-stealing pop-up offers "Send to apps". Detection reads Windows' CapabilityAccessManager usage records; this app's own camera use and dictation apps (Wispr Flow) are ignored.
- **Global shortcut Ctrl+Alt+O** toggles Send to apps, with a confirmation pop-up. It's also shown in the header and tray menu.
- **Auto-off**: Send to apps switches off after 3 minutes with no meeting, to stop using CPU for nothing. Configurable via `auto_off_minutes`.
- **Window fixes**: the panel is now resizable/maximizable (preview scales, controls moved to a tabbed card). The first open fits the monitor (including portrait screens). Minimize releases the camera when not sending, and restore resumes the preview.
- New tray item "Meeting pop-ups". "Send to apps" now defaults to off for new installs, since the pop-up turns it on when needed.

## 2026-10-15 (update)
- **Fixed zoom / pan / tilt.** The Owl Lite firmware accepts hardware PTZ values but ignores them; captured frames were identical at zoom 0 and 10. Framing is now done in software (`pipeline.py`): crop the 1080p feed and publish it to OBS Virtual Camera, so Zoom/Teams/Meet get the framed picture. Removed the non-working hardware PTZ sliders.
- **New premium UI** built with CustomTkinter: dark card layout, live preview with drag-to-pan and scroll-to-zoom, a vertical tilt slider, status pill, "Send to apps" switch, and Auto switches. Presets now include framing.
- Camera preview opens in ~1 s instead of ~6 s (format passed at open time).
- Panel opens on the monitor where you clicked, sized correctly on mixed-DPI setups.

## 2026-10-15
- Initial release. Tray app that controls the Owl Lite Camera's hardware settings (zoom, pan/tilt, focus, exposure, white balance, brightness, contrast, saturation, hue, sharpness, gamma, gain, backlight) with a live preview. Added named presets, auto-restore at login and on replug, and a start-with-Windows toggle. The Windows Camera app doesn't expose these controls, and settings applied in the camera itself work in every app.
