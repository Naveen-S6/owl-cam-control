"""Tests for the pop-up window. Run: .venv\\Scripts\\python -m unittest"""
import unittest

import customtkinter as ctk

from ui import Toast


class ToastTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_survives_monitor_dpi_change(self):
        """Regression: a pop-up on a monitor with different scaling crashed CTk's DPI tracker."""
        toast = Toast(self.root, "Looks like a meeting is starting", "Zoom is using your camera.",
                      actions=[("Send to apps", lambda: None, True)])
        self.root.update()
        ctk.ScalingTracker.window_dpi_scaling_dict[toast.win] = 9.0  # pretend the monitor's DPI changed
        ctk.ScalingTracker.check_dpi_scaling()  # raised AttributeError before the fix
        toast.close()


if __name__ == "__main__":
    unittest.main()
