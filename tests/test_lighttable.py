import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apps.lighttable.core import IMAGE_EXTS, scan_images, thumb_key, thumb_path_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class ScanTests(unittest.TestCase):
    def test_recursive_newest_first_and_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub/deeper").mkdir(parents=True)
            old = root / "old.png"
            mid = root / "sub/mid.tif"
            new = root / "sub/deeper/new.jpg"
            for i, path in enumerate((old, mid, new)):
                path.touch()
                os.utime(path, (time.time(), time.time() + i))
            (root / "notes.txt").touch()
            (root / ".hidden.png").touch()
            result = [Path(p).name for _m, p in scan_images(root)]
            self.assertEqual(["new.jpg", "mid.tif", "old.png"], result)

    def test_limit_caps_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(10):
                (Path(tmp) / f"img_{i}.png").touch()
            self.assertEqual(3, len(scan_images(Path(tmp), limit=3)))

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual([], scan_images(Path("/nonexistent/nowhere")))


class ThumbCacheTests(unittest.TestCase):
    def test_key_tracks_freshness(self) -> None:
        a = thumb_key("/x/a.png", 100.0, 5)
        self.assertEqual(a, thumb_key("/x/a.png", 100.0, 5))
        self.assertNotEqual(a, thumb_key("/x/a.png", 101.0, 5))
        self.assertNotEqual(a, thumb_key("/x/b.png", 100.0, 5))

    def test_thumb_path_for_missing_file_is_none(self) -> None:
        self.assertIsNone(thumb_path_for("/nonexistent/nowhere.png"))

    def test_common_extensions_registered(self) -> None:
        self.assertLessEqual({".png", ".jpg", ".tif", ".tiff", ".webp", ".gif"}, IMAGE_EXTS)


class LightTableWindowSmokeTests(unittest.TestCase):
    def test_window_constructs_and_scans_offscreen(self) -> None:
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication

        from apps.lighttable.app import LightTableWindow, load_frames

        app = QApplication.instance() or QApplication([])
        del app
        with tempfile.TemporaryDirectory() as tmp:
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(0xFF202020)
            image.save(str(Path(tmp) / "shot.png"))
            self.assertEqual(1, len(load_frames(str(Path(tmp) / "shot.png"))))
            window = LightTableWindow(Path(tmp))
            tab = window.current_tab()
            self.assertEqual(1, tab.sheet.count())
            created = tab.new_folder("picks")
            self.assertTrue(created is not None and created.is_dir())
            self.assertIsNone(tab.new_folder("picks"))
            window.toggle_focus()
            self.assertFalse(tab.tree.isVisibleTo(tab))
            window.toggle_focus()
            window.close()


if __name__ == "__main__":
    unittest.main()
