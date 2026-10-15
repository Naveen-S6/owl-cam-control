"""Tests for settings persistence. Run: .venv\\Scripts\\python -m unittest"""
import tempfile
import unittest
from pathlib import Path

import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_gives_defaults(self):
        self.assertEqual(store.load(self.path), store.default_settings())

    def test_round_trip(self):
        data = store.default_settings()
        data["presets"]["Meeting"] = {"zoom": {"value": 4, "auto": False}}
        store.save(data, self.path)
        self.assertEqual(store.load(self.path), data)

    def test_corrupt_file_is_backed_up_not_lost(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(store.load(self.path), store.default_settings())
        self.assertTrue(self.path.with_suffix(".corrupt.json").exists())

    def test_unknown_keys_ignored(self):
        self.path.write_text('{"presets": {"A": {}}, "evil": 1}', encoding="utf-8")
        data = store.load(self.path)
        self.assertNotIn("evil", data)
        self.assertEqual(data["presets"], {"A": {}})

    def test_preset_name_validation(self):
        self.assertEqual(store.clean_preset_name("  Meeting "), "Meeting")
        for bad in ("", "   ", None, "x" * 41):
            with self.assertRaises(ValueError):
                store.clean_preset_name(bad)


if __name__ == "__main__":
    unittest.main()
