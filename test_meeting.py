"""Tests for meeting detection logic. Run: .venv\\Scripts\\python -m unittest"""
import unittest

from meeting import MeetingWatcher, app_label


class LabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(app_label(r"C:#Users#x#AppData#Roaming#Zoom#bin#Zoom.exe"), "Zoom")
        self.assertEqual(app_label("MSTeams_8wekyb3d8bbwe"), "MSTeams")
        self.assertEqual(app_label("Microsoft.WindowsCamera_8wekyb3d8bbwe"), "WindowsCamera")


class WatcherTests(unittest.TestCase):
    def test_meeting_announced_once_after_delay(self):
        w = MeetingWatcher(start_delay=8, idle_off=180)
        self.assertEqual(w.update(["Zoom"], 0, False), [])
        self.assertEqual(w.update(["Zoom"], 6, False), [])
        self.assertEqual(w.update(["Zoom"], 8, False), [("meeting", ["Zoom"])])
        self.assertEqual(w.update(["Zoom"], 10, False), [])

    def test_short_mic_use_is_ignored(self):
        w = MeetingWatcher(start_delay=8)
        w.update(["Chrome"], 0, False)
        w.update([], 4, False)
        self.assertEqual(w.update(["Chrome"], 10, False), [])  # restarted, not 10 s continuous

    def test_idle_off_after_no_meeting(self):
        w = MeetingWatcher(idle_off=180)
        w.update([], 0, False)
        self.assertEqual(w.update([], 100, True), [])  # sending just turned on: clock starts at 0
        self.assertEqual(w.update([], 179, True), [])
        self.assertEqual(w.update([], 181, True), [("idle_off",)])

    def test_meeting_keeps_sending_alive(self):
        w = MeetingWatcher(start_delay=8, idle_off=180)
        w.update([], 0, True)
        w.update(["Zoom"], 100, True)
        self.assertEqual(w.update([], 250, True), [])  # idle clock restarted when meeting ended at 100+
        self.assertEqual(w.update([], 281, True), [("idle_off",)])


if __name__ == "__main__":
    unittest.main()
