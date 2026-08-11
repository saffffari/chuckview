import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apps.lighttable.core import (  # noqa: E402
    IMAGE_EXTS,
    add_favorite,
    default_favorites,
    is_image_path,
    load_favorites,
    mounted_drives,
    remove_favorite,
    save_favorites,
    tree_anchor,
    open_path_command,
    pasted_image_name,
    plan_import,
    reveal_command,
    scan_images,
    thumb_key,
    thumb_path_for,
    trash_command,
    unique_destination,
)

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


class UniqueDestinationTests(unittest.TestCase):
    def test_free_name_is_used_as_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(Path(tmp) / "a.png", unique_destination(Path(tmp), "a.png"))

    def test_disk_collision_gets_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.png").touch()
            self.assertEqual(Path(tmp) / "a (2).png", unique_destination(Path(tmp), "a.png"))

    def test_reserved_names_are_avoided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.png").touch()
            reserved = [Path(tmp) / "a (2).png"]
            self.assertEqual(Path(tmp) / "a (3).png", unique_destination(Path(tmp), "a.png", reserved))


class PlanImportTests(unittest.TestCase):
    def test_external_images_copy_inside_stay(self) -> None:
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            inside = Path(folder) / "here.png"
            inside.touch()
            outside = Path(other) / "there.jpg"
            outside.touch()
            not_image = Path(other) / "notes.txt"
            not_image.touch()
            plan = plan_import([str(inside), str(outside), str(not_image)], Path(folder))
            self.assertEqual((inside.resolve(),), plan.existing)
            self.assertEqual(1, len(plan.copies))
            src, dst = plan.copies[0]
            self.assertEqual(outside.resolve(), src)
            self.assertEqual(Path(folder).resolve() / "there.jpg", dst)

    def test_duplicate_external_names_get_distinct_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as folder, \
                tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            one = Path(a) / "shot.png"
            two = Path(b) / "shot.png"
            one.touch()
            two.touch()
            plan = plan_import([str(one), str(two)], Path(folder))
            destinations = [dst.name for _src, dst in plan.copies]
            self.assertEqual(["shot.png", "shot (2).png"], destinations)

    def test_missing_and_duplicate_sources_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "there.jpg"
            outside.touch()
            plan = plan_import([str(outside), str(outside), str(Path(other) / "gone.png")], Path(folder))
            self.assertEqual(1, len(plan.copies))
            self.assertEqual((), plan.existing)


class PastedNameTests(unittest.TestCase):
    def test_stamps_a_png_name(self) -> None:
        self.assertEqual(
            "Pasted 2026-07-23 143005.png",
            pasted_image_name(datetime(2026, 7, 23, 14, 30, 5)),
        )


class FileManagerVerbTests(unittest.TestCase):
    def test_reveal_selects_the_file_per_os(self) -> None:
        self.assertEqual(["open", "-R", "/a/b.png"], reveal_command("/a/b.png", "darwin"))
        self.assertEqual(["explorer", "/select,/a/b.png"], reveal_command("/a/b.png", "win32"))
        linux = reveal_command("/a/b.png", "linux")
        self.assertEqual("dbus-send", linux[0])
        self.assertIn("org.freedesktop.FileManager1.ShowItems", linux)
        self.assertIn("array:string:file:///a/b.png", linux)

    def test_open_path_falls_back_per_os(self) -> None:
        self.assertEqual(["open", "/a"], open_path_command("/a", "darwin"))
        self.assertEqual(["explorer", "/a"], open_path_command("/a", "win32"))
        self.assertEqual(["xdg-open", "/a"], open_path_command("/a", "linux"))

    def test_trash_is_recoverable_or_none(self) -> None:
        self.assertEqual(["gio", "trash", "--", "/a/b.png"], trash_command("/a/b.png", "linux"))
        self.assertEqual("osascript", trash_command("/a/b.png", "darwin")[0])
        self.assertIsNone(trash_command("/a/b.png", "win32"))

    def test_is_image_path(self) -> None:
        self.assertTrue(is_image_path("/x/Y.PNG"))
        self.assertFalse(is_image_path("/x/y.txt"))


class FavoritesTests(unittest.TestCase):
    """Favorites are a flat text file — pin, unpin, hand-edit, all the same file."""

    def test_missing_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            favorites = load_favorites(Path(tmp) / "nope.txt")
            self.assertEqual(default_favorites(), favorites)
            self.assertIn(Path.home(), favorites)

    def test_pin_and_unpin_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "favorites.txt"
            folder = Path(tmp) / "shots"
            folder.mkdir()
            add_favorite(folder, store)
            self.assertIn(folder, load_favorites(store))
            add_favorite(folder, store)  # pinning twice is not two entries
            self.assertEqual(1, load_favorites(store).count(folder))
            remove_favorite(folder, store)
            self.assertNotIn(folder, load_favorites(store))
            remove_favorite(folder, store)  # unpinning what is not pinned is a no-op

    def test_file_is_human_readable_and_hand_editable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "favorites.txt"
            save_favorites([Path(tmp) / "a", Path(tmp) / "b"], store)
            text = store.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#"))
            self.assertIn(f"{tmp}/a", text)
            store.write_text(f"# mine\n\n{tmp}/c\n  {tmp}/d  \n", encoding="utf-8")
            self.assertEqual([Path(tmp) / "c", Path(tmp) / "d"], load_favorites(store))

    def test_unplugged_favorite_is_kept_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "favorites.txt"
            save_favorites(["/run/media/alex/citadel"], store)
            add_favorite(Path(tmp), store)
            self.assertIn(Path("/run/media/alex/citadel"), load_favorites(store))


class DriveTests(unittest.TestCase):
    """Drives are read live off the mount table, never stored."""

    class _Stat:
        def __init__(self, device: int) -> None:
            self.st_dev = device

    def _as_mounts(self, devices: dict[str, int]):
        """Pretend the given paths are mount points on the given devices. Temp dirs all
        share one filesystem, so both facts have to be faked to test the real rules."""
        return (
            mock.patch("apps.lighttable.core.os.path.ismount",
                       side_effect=lambda path: str(path) in devices),
            mock.patch("apps.lighttable.core.os.stat",
                       side_effect=lambda path: self._Stat(devices[str(path)])),
        )

    def test_mounted_volumes_are_listed_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("citadel", "board_rack"):
                (Path(tmp) / name).mkdir()
            (Path(tmp) / ".hidden").mkdir()
            (Path(tmp) / "notes.txt").touch()
            ismount, stat = self._as_mounts({f"{tmp}/citadel": 1, f"{tmp}/board_rack": 2})
            with ismount, stat:
                found = mounted_drives(roots=[Path(tmp), Path(tmp) / "nonexistent"])
            self.assertEqual(["board_rack", "citadel"], [label for label, _p in found])

    def test_empty_placeholder_directories_are_not_drives(self) -> None:
        # /mnt is full of empty directories waiting for a disk. None of them is a drive.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "citadel").mkdir()
            self.assertEqual([], mounted_drives(roots=[Path(tmp)]))

    def test_one_disk_mounted_twice_appears_once(self) -> None:
        with tempfile.TemporaryDirectory() as run, tempfile.TemporaryDirectory() as mnt:
            (Path(run) / "citadel").mkdir()
            (Path(mnt) / "citadel").mkdir()
            ismount, stat = self._as_mounts({f"{run}/citadel": 7, f"{mnt}/citadel": 7})
            with ismount, stat:
                found = mounted_drives(roots=[Path(run), Path(mnt)])
            self.assertEqual([(("citadel"), Path(run) / "citadel")], found)


class TreeAnchorTests(unittest.TestCase):
    def test_folder_on_a_drive_anchors_to_that_drive(self) -> None:
        mounts = [Path("/run/media/alex/citadel"), Path("/run/media/alex/board_rack")]
        self.assertEqual(
            Path("/run/media/alex/citadel"),
            tree_anchor("/run/media/alex/citadel/2026/shoot", mounts))

    def test_folder_under_home_anchors_to_home(self) -> None:
        self.assertEqual(Path.home(), tree_anchor(Path.home() / "Pictures", []))

    def test_folder_outside_everything_anchors_to_the_root(self) -> None:
        self.assertEqual(Path("/"), tree_anchor("/opt/renders", []))


class LightTableWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        # Keep the reference: in PyQt6 an unreferenced QApplication is destroyed
        # immediately, and the next QWidget constructor aborts the process.
        cls.app = QApplication.instance() or QApplication([])

    def test_window_constructs_and_scans_offscreen(self) -> None:
        from PyQt6.QtCore import QThreadPool
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow, load_frames

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
            # Thumbnails must land in a temporary cache, never ~/.cache/2photon.
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
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
                # Light Table opens directly in Focus: locations and gallery hidden.
                self.assertFalse(tab.location_column.isVisibleTo(tab))
                self.assertFalse(tab.gallery_column.isVisibleTo(tab))
                self.assertTrue(tab.pane_visible("viewer"))
                window.toggle_focus()
                self.assertTrue(tab.pane_visible("locations"))
                self.assertTrue(tab.pane_visible("gallery"))
                window.toggle_focus()

                # Import an external image (drag/drop or paste-of-files path).
                with tempfile.TemporaryDirectory() as other:
                    external = Path(other) / "dropped.png"
                    image.save(str(external))
                    tab.import_and_view([str(external)])
                    self.assertTrue((Path(tmp) / "dropped.png").is_file())
                    self.assertEqual(2, tab.sheet.count())
                    self.assertEqual(str((Path(tmp) / "dropped.png").resolve()),
                                     str(Path(tab.current_path()).resolve()))

                # Paste raw bitmap data → a timestamped PNG lands in the folder.
                before = tab.sheet.count()
                tab.save_and_view_image(image)
                self.assertEqual(before + 1, tab.sheet.count())
                self.assertTrue(any(p.name.startswith("Pasted ") for p in Path(tmp).glob("Pasted *.png")))

                # Copy the selection to the clipboard as file URLs (+ bitmap).
                tab.copy_selection()
                from PyQt6.QtGui import QGuiApplication
                self.assertTrue(QGuiApplication.clipboard().mimeData().hasUrls())

                # The context menu builds with the standard verbs.
                labels = [a.text() for a in tab.build_context_menu().actions() if a.text()]
                self.assertTrue(any("Reveal in File Manager" in text for text in labels))
                self.assertTrue(any(text.startswith("Copy") for text in labels))
                self.assertTrue(any("Trash" in text for text in labels))

                # The folder menu is where a folder gets pinned.
                folder_menu = [a.text() for a in tab.build_folder_menu(Path(tmp)).actions()]
                self.assertIn("Add to Favorites", folder_menu)

                # Wait out the thumbnail workers so no ThumbJob outlives the
                # test — a live worker at interpreter shutdown aborts Qt.
                QThreadPool.globalInstance().waitForDone(5000)
                window.close()

    def test_disclosure_rail_opens_each_column(self) -> None:
        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()
                # Focus: both columns closed, both disclosure tabs unchecked.
                self.assertFalse(any(b.isChecked() for b in tab.rail.buttons.values()))
                tab.rail.buttons["gallery"].click()
                self.assertTrue(tab.pane_visible("gallery"))
                self.assertFalse(tab.pane_visible("locations"))
                # Opening a column drops Focus; the toolbar toggle follows along.
                self.assertTrue(window.pane_actions["gallery"].isChecked())
                self.assertFalse(window.focus_action.isChecked())
                tab.rail.buttons["gallery"].click()
                self.assertFalse(tab.pane_visible("gallery"))
                self.assertTrue(window.focus_action.isChecked())
                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()

    def test_places_lists_favorites_then_drives_and_pins(self) -> None:
        from apps.lighttable.app import FAVORITE_ROLE, PATH_ROLE, LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                shoot = Path(tmp) / "shoot"
                shoot.mkdir()
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()
                window.new_tab(Path(tmp))
                other = window.current_tab()

                tab._set_favorite(shoot, True)
                labels = [tab.places.item(row).text() for row in range(tab.places.count())]
                self.assertIn("shoot", labels)
                # A favorite pinned in one tab shows up in the other: one file underneath.
                self.assertIn("shoot", [other.places.item(row).text()
                                        for row in range(other.places.count())])
                pinned = next(tab.places.item(row) for row in range(tab.places.count())
                              if tab.places.item(row).text() == "shoot")
                self.assertTrue(pinned.data(FAVORITE_ROLE))
                self.assertEqual(str(shoot), pinned.data(PATH_ROLE))
                # Clicking a place drives the folder, tree, and contact sheet together.
                tab.places.itemClicked.emit(pinned)
                self.assertEqual(shoot, tab.folder)

                tab._set_favorite(shoot, False)
                self.assertNotIn("shoot", [tab.places.item(row).text()
                                           for row in range(tab.places.count())])
                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()

    def test_viewport_drag_carries_the_file_and_the_bitmap(self) -> None:
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
                shot = Path(tmp) / "shot.png"
                image = QImage(8, 8, QImage.Format.Format_RGB32)
                image.fill(0xFF202020)
                image.save(str(shot))
                window = LightTableWindow(shot)
                view = window.current_tab().view
                self.assertEqual(str(shot), view.source_path)

                carried = {}

                class FakeDrag:
                    def __init__(self, _parent) -> None:
                        pass

                    def setMimeData(self, data) -> None:
                        carried["mime"] = data

                    def setPixmap(self, pixmap) -> None:
                        carried["pixmap"] = pixmap

                    def setHotSpot(self, point) -> None:
                        pass

                    def exec(self, action):
                        carried["exec"] = action
                        return action

                with mock.patch("apps.lighttable.app.QDrag", FakeDrag):
                    view.drag_out()
                mime = carried["mime"]
                self.assertEqual([str(shot)], [u.toLocalFile() for u in mime.urls()])
                self.assertTrue(mime.hasImage())
                self.assertIn("pixmap", carried)

                view.source_path = str(Path(tmp) / "gone.png")
                carried.clear()
                with mock.patch("apps.lighttable.app.QDrag", FakeDrag):
                    view.drag_out()  # nothing to carry, nothing happens
                self.assertEqual({}, carried)
                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()


if __name__ == "__main__":
    unittest.main()
