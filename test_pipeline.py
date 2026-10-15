"""Tests for the software framing math. Run: .venv\\Scripts\\python -m unittest"""
import unittest

import numpy as np

from pipeline import apply_framing, crop_rect

ASPECT = 16 / 9


class CropTests(unittest.TestCase):
    def test_no_zoom_is_full_frame(self):
        self.assertEqual(crop_rect(1920, 1080, 1.0, 0, 0, ASPECT), (0, 0, 1920, 1080))

    def test_zoom_2_centered(self):
        self.assertEqual(crop_rect(1920, 1080, 2.0, 0, 0, ASPECT), (480, 270, 960, 540))

    def test_offsets_reach_edges_and_stay_inside(self):
        self.assertEqual(crop_rect(1920, 1080, 2.0, -100, -100, ASPECT)[:2], (0, 0))
        left, top, w, h = crop_rect(1920, 1080, 2.0, 100, 100, ASPECT)
        self.assertEqual((left + w, top + h), (1920, 1080))

    def test_out_of_range_values_are_clamped(self):
        self.assertEqual(crop_rect(1920, 1080, 99, 500, -500, ASPECT), crop_rect(1920, 1080, 4.0, 100, -100, ASPECT))

    def test_4_3_source_keeps_16_9_output_unstretched(self):
        left, top, w, h = crop_rect(640, 480, 1.0, 0, 0, ASPECT)
        self.assertAlmostEqual(w / h, ASPECT, places=2)
        self.assertEqual((left, w), (0, 640))

    def test_apply_framing_output_size(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        out = apply_framing(frame, {"frame_zoom": 3.0, "frame_x": 50, "frame_y": -20}, (1280, 720))
        self.assertEqual(out.shape, (720, 1280, 3))


if __name__ == "__main__":
    unittest.main()
