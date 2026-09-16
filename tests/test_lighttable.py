import contextlib
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QRect, QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QMenu  # noqa: E402

from apps.lighttable import glass, panels  # noqa: E402
from apps.lighttable.app import DEFAULT_PANELS  # noqa: E402
from apps.lighttable.panels import PanelState  # noqa: E402
from apps.lighttable.core import (  # noqa: E402
    IMAGE_EXTS,
    add_favorite,
    collection_file,
    collection_members,
    default_favorites,
    filter_paths,
    is_image_path,
    load_collection,
    load_favorites,
    load_marks,
    mounted_drives,
    open_path_command,
    pasted_image_name,
    plan_import,
    remove_favorite,
    reveal_command,
    save_collection,
    save_favorites,
    scan_images,
    thumb_key,
    thumb_path_for,
    toggle_collection,
    trash_command,
    tree_anchor,
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


class FilterTests(unittest.TestCase):
    def test_matches_every_word_in_any_order(self) -> None:
        paths = ["/a/car_red_04.png", "/a/red_house.png", "/a/blue_car.png"]
        self.assertEqual(["/a/car_red_04.png"], filter_paths(paths, "red car"))
        self.assertEqual(["/a/car_red_04.png"], filter_paths(paths, "car red"))

    def test_is_case_insensitive(self) -> None:
        paths = ["/a/one.png", "/a/SHOOT_two.png"]
        self.assertEqual(["/a/SHOOT_two.png"], filter_paths(paths, "shoot"))

    def test_subfolders_below_the_root_are_searchable(self) -> None:
        # The sheet is recursive, so "apollo" has to find what is inside apollo_11_retro/.
        paths = ["/pics/apollo_11_retro/a.png", "/pics/other/b.png", "/pics/apollo.png"]
        self.assertEqual(["/pics/apollo_11_retro/a.png", "/pics/apollo.png"],
                         filter_paths(paths, "apollo", "/pics"))

    def test_the_folder_you_are_in_does_not_match_everything(self) -> None:
        # Without this, searching "pics" inside /pics would match every image.
        paths = ["/pics/a.png", "/pics/b.png"]
        self.assertEqual([], filter_paths(paths, "pics", "/pics"))
        self.assertEqual([], filter_paths(paths, "pics", "/pics/"))

    def test_empty_query_keeps_everything_in_order(self) -> None:
        paths = ["/a/b.png", "/a/a.png"]
        self.assertEqual(paths, filter_paths(paths, ""))
        self.assertEqual(paths, filter_paths(paths, "   "))

    def test_no_match_is_empty(self) -> None:
        self.assertEqual([], filter_paths(["/a/b.png"], "nothing"))


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

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            # Thumbnails must land in a temporary cache, never ~/.cache/2photon.
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
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

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
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

    def test_search_narrows_the_sheet_and_opens_the_gallery(self) -> None:
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                image = QImage(8, 8, QImage.Format.Format_RGB32)
                image.fill(0xFF202020)
                for name in ("red_car.png", "blue_car.png", "house.png"):
                    image.save(str(Path(tmp) / name))
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()
                self.assertEqual(3, tab.sheet.count())
                self.assertFalse(tab.pane_visible("gallery"))  # opens in Focus

                window.search.setText("car")
                self.assertEqual(2, tab.sheet.count())
                self.assertEqual(3, len(tab.all_paths))  # the folder is untouched
                # Results you cannot see are not results.
                self.assertTrue(tab.pane_visible("gallery"))
                self.assertIn("2 of 3 images", tab.folder_info.text())

                window.search.setText("red car")
                self.assertEqual(1, tab.sheet.count())
                window.search.setText("nothing here")
                self.assertEqual(0, tab.sheet.count())

                # Escape drops the filter and hands the keyboard back to the picture.
                window._escape()
                self.assertEqual("", window.search.text())
                self.assertEqual(3, tab.sheet.count())

                # Search is per tab: a new tab starts unfiltered, and switching back
                # restores the box without re-running the filter.
                window.search.setText("house")
                window.new_tab(Path(tmp))
                self.assertEqual("", window.search.text())
                self.assertEqual(3, window.current_tab().sheet.count())
                window.tabs.setCurrentIndex(0)
                self.assertEqual("house", window.search.text())
                self.assertEqual(1, tab.sheet.count())

                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()

    def test_horizontal_wheel_scrolls_instead_of_stepping_images(self) -> None:
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QImage, QKeyEvent, QWheelEvent

        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                image = QImage(8, 8, QImage.Format.Format_RGB32)
                image.fill(0xFF202020)
                for name in ("a.png", "b.png"):
                    image.save(str(Path(tmp) / name))
                window = LightTableWindow(Path(tmp))
                view = window.current_tab().view
                steps: list[int] = []
                view.request_step.connect(steps.append)
                panned: list[tuple[float, float]] = []
                view.translate_view = lambda dx, dy: panned.append((dx, dy))

                def wheel(dx: int, dy: int) -> None:
                    view.wheelEvent(QWheelEvent(
                        QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(dx, dy),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False))

                # The horizontal wheel scrolls the picture sideways, both directions,
                # and no longer changes which image is on screen.
                wheel(120, 0)
                wheel(-120, 0)
                self.assertEqual([(120, 0), (-120, 0)], panned)
                self.assertEqual([], steps)

                # Vertical wheel still zooms a flat image, and still does not step.
                before = view.transform().m11()
                wheel(0, 120)
                self.assertGreater(view.transform().m11(), before)
                self.assertEqual([], steps)

                # Arrows still go to the next picture, so stepping did not go away.
                view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right.value,
                                             Qt.KeyboardModifier.NoModifier))
                self.assertEqual([1], steps)

                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()

    def test_viewport_drag_carries_the_file_and_the_bitmap(self) -> None:
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
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


class CollectionTests(unittest.TestCase):
    """The quick collection and the flags: two ordered lists of paths in two text files."""

    def setUp(self) -> None:
        self.config = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"TWOPHOTON_CONFIG_HOME": self.config.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.config.cleanup)

    def test_a_set_starts_empty_and_survives_a_round_trip(self) -> None:
        self.assertEqual([], load_collection("quick"))
        self.assertEqual([], collection_members("flagged"))
        save_collection("quick", ["/tmp/a.png", "/tmp/b.png"])
        self.assertEqual([Path("/tmp/a.png"), Path("/tmp/b.png")], load_collection("quick"))
        # Readable and editable by hand: a comment and some whitespace change nothing.
        collection_file("quick").write_text(
            "# mine\n/tmp/a.png\n\n  /tmp/c.png  \n", encoding="utf-8")
        self.assertEqual([Path("/tmp/a.png"), Path("/tmp/c.png")], load_collection("quick"))

    def test_one_key_both_directions(self) -> None:
        _set, added = toggle_collection("quick", ["/tmp/a.png"])
        self.assertTrue(added)
        self.assertEqual([Path("/tmp/a.png")], load_collection("quick"))
        _set, added = toggle_collection("quick", ["/tmp/a.png"])
        self.assertFalse(added)
        self.assertEqual([], load_collection("quick"))

    def test_a_mixed_selection_goes_in(self) -> None:
        """Some in, some out, one keypress: "all of these", not "undo half of them"."""
        toggle_collection("flagged", ["/tmp/a.png"])
        _set, added = toggle_collection("flagged", ["/tmp/a.png", "/tmp/b.png"])
        self.assertTrue(added)
        self.assertEqual([Path("/tmp/a.png"), Path("/tmp/b.png")], load_collection("flagged"))
        # Now that both are in, the same keypress takes both out.
        _set, added = toggle_collection("flagged", ["/tmp/a.png", "/tmp/b.png"])
        self.assertFalse(added)
        self.assertEqual([], load_collection("flagged"))

    def test_nothing_selected_changes_nothing(self) -> None:
        save_collection("quick", ["/tmp/a.png"])
        _set, added = toggle_collection("quick", [])
        self.assertFalse(added)
        self.assertEqual([Path("/tmp/a.png")], load_collection("quick"))

    def test_the_two_sets_are_independent_and_read_as_membership(self) -> None:
        toggle_collection("quick", ["/tmp/a.png"])
        toggle_collection("flagged", ["/tmp/b.png"])
        self.assertEqual({"quick": {"/tmp/a.png"}, "flagged": {"/tmp/b.png"}}, load_marks())

    def test_a_missing_file_stays_marked_but_is_not_shown(self) -> None:
        """An unplugged drive has not un-flagged anything — but the sheet can only show
        pictures that are actually there."""
        with tempfile.TemporaryDirectory() as tmp:
            here = Path(tmp) / "here.png"
            here.write_bytes(b"")
            save_collection("quick", [str(here), "/nowhere/gone.png"])
            self.assertEqual(2, len(load_collection("quick")))
            self.assertEqual([str(here)], collection_members("quick"))

    def test_the_order_is_the_order_you_marked_them(self) -> None:
        toggle_collection("quick", ["/tmp/b.png"])
        toggle_collection("quick", ["/tmp/a.png"])
        self.assertEqual([Path("/tmp/b.png"), Path("/tmp/a.png")], load_collection("quick"))


class GlassTests(unittest.TestCase):
    """The material: read off the desktop, and computed only where glass bends light."""

    def test_settings_come_from_the_desktops_own_effect(self) -> None:
        text = ("[Effect-other]\nRefractionStrength=99\n\n"
                "[Effect-blurplus]\nTopCornerRadius=20\nRefractionEdgeSize=8\n"
                "RimGlow=false\nNoiseStrength=nonsense\n\n[Effect-later]\nRimGlow=true\n")
        values = glass.read_group(text, glass.KWIN_GROUP)
        self.assertEqual({"TopCornerRadius": "20", "RefractionEdgeSize": "8",
                          "RimGlow": "false", "NoiseStrength": "nonsense"}, values)
        settings = glass.settings_from_group(values)
        self.assertEqual(20.0, settings.top_radius)
        self.assertEqual(8.0, settings.edge)
        self.assertFalse(settings.rim_glow)
        # A value that will not parse keeps the default rather than taking the glass down.
        self.assertEqual(glass.DEFAULTS.noise, settings.noise)
        self.assertEqual(glass.DEFAULTS.strength, settings.strength)

    def test_no_kwin_no_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(glass.DEFAULTS,
                             glass.read_glass_settings(Path(tmp) / "not-here"))

    def test_the_distance_field_knows_inside_from_edge(self) -> None:
        sdf = glass.rounded_box_sdf(60, 40, 10)
        self.assertLess(sdf[20, 30], -10)          # the middle is deep inside
        # The left-hand pixel centre sits half a pixel inside the edge.
        self.assertAlmostEqual(sdf[20, 0], -0.5, places=4)
        self.assertGreater(sdf[0, 0], 0)           # the corner is outside the round
        self.assertEqual((40, 60), sdf.shape)

    def test_the_glass_is_only_computed_where_it_bends(self) -> None:
        maps = glass.Maps(200, 160, glass.DEFAULTS)
        self.assertLess(maps.band.size, 200 * 160 * 0.35)   # a band, not the whole pane
        rows, columns = np.divmod(maps.band, 200)
        self.assertEqual(0, rows.min())                      # and it reaches every edge
        self.assertEqual(159, rows.max())
        self.assertEqual(0, columns.min())
        self.assertEqual(199, columns.max())
        middle = 80 * 200 + 100
        self.assertNotIn(middle, set(maps.band.tolist()))

    def test_the_flat_middle_comes_back_transparent_and_the_edge_solid(self) -> None:
        backdrop = np.full((160, 200, 3), 200, dtype=np.uint8)
        out = glass.MaterialCache(glass.DEFAULTS).rim_image(backdrop, 200, 160,
                                                            (13, 13, 13), 0.62)
        self.assertEqual((160, 200, 4), out.shape)
        self.assertEqual(0, out[80, 100, 3])     # middle: Qt fills that, not us
        self.assertEqual(255, out[80, 0, 3])     # edge: ours
        self.assertEqual(0, out[0, 0, 3])        # outside the rounded corner: nothing
        # Tinted, so the edge is darker than the backdrop it is standing on.
        self.assertLess(int(out[80, 1, 0]), 200)

    def test_refraction_disperses_the_channels(self) -> None:
        """Physically based refraction bends red furthest and blue least, which is why
        the three channels read from three different places."""
        maps = glass.Maps(200, 160, glass.DEFAULTS)
        red, green, blue = maps.samples
        self.assertTrue((red != blue).any())
        self.assertTrue((red != green).any())
        flat = glass.Maps(200, 160, replace(glass.DEFAULTS, physical=False))
        self.assertTrue((flat.samples[0] == flat.samples[2]).all())

    def test_maps_are_cached_per_size(self) -> None:
        cache = glass.MaterialCache(glass.DEFAULTS, limit=2)
        first = cache.maps(100, 100)
        self.assertIs(first, cache.maps(100, 100))
        cache.maps(120, 100)
        cache.maps(140, 100)          # over the limit: the oldest goes
        self.assertIsNot(first, cache.maps(100, 100))


class PanelLayoutTests(unittest.TestCase):
    """Where the panels are: a rectangle each, and one line of text each on disk."""

    AREA = QRect(0, 40, 1000, 760)

    def test_docking_takes_the_whole_edge_under_the_command_bar(self) -> None:
        left = panels.snap_geometry(PanelState("locations", width=300, snap="left"),
                                    self.AREA)
        self.assertEqual((0, 40, 300, 760), left.getRect())
        right = panels.snap_geometry(PanelState("gallery", width=380, snap="right"),
                                     self.AREA)
        self.assertEqual((620, 40, 380, 760), right.getRect())
        bottom = panels.snap_geometry(PanelState("info", height=150, snap="bottom"),
                                      self.AREA)
        self.assertEqual((0, 650, 1000, 150), bottom.getRect())

    def test_a_free_panel_can_never_be_dragged_out_of_reach(self) -> None:
        stray = PanelState("info", x=-4000, y=9000, width=300, height=200)
        rect = panels.snap_geometry(stray, self.AREA)
        self.assertGreaterEqual(rect.right(), self.AREA.left())
        self.assertLessEqual(rect.top(), self.AREA.bottom())
        self.assertGreaterEqual(rect.top(), self.AREA.top())

    def test_only_a_panel_let_go_of_at_an_edge_docks(self) -> None:
        """Where the pointer was, not where the panel was: a tall panel is near the top
        and the bottom at once wherever you put it, so the panel cannot be the judge."""
        self.assertEqual("left", panels.snap_for(QPoint(6, 400), self.AREA))
        self.assertEqual("right", panels.snap_for(QPoint(995, 400), self.AREA))
        self.assertEqual("top", panels.snap_for(QPoint(500, 46), self.AREA))
        self.assertEqual("bottom", panels.snap_for(QPoint(500, 790), self.AREA))
        self.assertEqual("free", panels.snap_for(QPoint(500, 400), self.AREA))
        # A tall panel dropped in open water floats, however close its own edges are.
        self.assertEqual("free", panels.snap_for(QPoint(360, 372), self.AREA))

    def test_two_panels_on_one_edge_are_a_column_of_two(self) -> None:
        states = [PanelState("locations", width=300, snap="left", open=True),
                  PanelState("gallery", width=340, snap="left", open=True)]
        rects = panels.share_edge(states, self.AREA)
        self.assertEqual(40, rects["locations"].top())
        self.assertEqual(rects["locations"].bottom() + 1, rects["gallery"].top())
        self.assertEqual(self.AREA.bottom(), rects["gallery"].bottom())
        # Each keeps its own width — sharing an edge is not sharing a size.
        self.assertEqual(300, rects["locations"].width())
        self.assertEqual(340, rects["gallery"].width())

    def test_rolled_up_gives_the_edge_back(self) -> None:
        """A collapsed panel is a header, not a full-height title bar — docked or not."""
        rolled = PanelState("locations", width=300, snap="left", collapsed=True)
        self.assertEqual(panels.HEADER,
                         panels.snap_geometry(rolled, self.AREA).height())

        states = [rolled, PanelState("gallery", width=340, snap="left", open=True)]
        rects = panels.share_edge(states, self.AREA)
        self.assertEqual(panels.HEADER, rects["locations"].height())
        # The gallery gets everything the rolled-up one is not using.
        self.assertEqual(rects["locations"].bottom() + 1, rects["gallery"].top())
        self.assertEqual(self.AREA.bottom(), rects["gallery"].bottom())

    def test_the_layout_is_one_readable_line_per_panel(self) -> None:
        states = [PanelState("locations", 10, 20, 300, 500, True, "left", False),
                  PanelState("gallery", 40, 50, 380, 600, False, "free", True)]
        text = panels.format_layout(states)
        self.assertIn("locations 10 20 300 500 1 left 0", text)
        self.assertIn("gallery 40 50 380 600 0 free 1", text)
        parsed = panels.parse_layout(text, DEFAULT_PANELS)
        self.assertEqual(states[0], parsed["locations"])
        self.assertEqual(states[1], parsed["gallery"])
        # A panel the file says nothing about keeps its default.
        self.assertEqual("info", parsed["info"].name)

    def test_a_damaged_layout_falls_back_instead_of_failing(self) -> None:
        defaults = {state.name: state for state in DEFAULT_PANELS}
        text = ("# comment\nlocations not a number here at all\n"
                "nonsense 1 2 3 4 1 left 0\ngallery 5 6 300 400 1 sideways 0\n")
        parsed = panels.parse_layout(text, DEFAULT_PANELS)
        self.assertEqual(defaults["locations"], parsed["locations"])
        self.assertNotIn("nonsense", parsed)
        self.assertEqual("free", parsed["gallery"].snap)  # an edge we do not have

    def test_a_missing_layout_file_is_a_fresh_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = panels.load_layout(DEFAULT_PANELS, Path(tmp) / "never-written.txt")
            self.assertEqual({state.name: state for state in DEFAULT_PANELS}, loaded)


class MarkingSmokeTests(unittest.TestCase):
    """A and F, end to end, through the window that has to make them mean something."""

    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def build(self, stack, count=3):
        """A window over `count` images in a throwaway folder, cache and config."""
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow

        tmp = stack.enter_context(tempfile.TemporaryDirectory())
        cache = stack.enter_context(tempfile.TemporaryDirectory())
        config = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(mock.patch.dict(os.environ, {
            "XDG_CACHE_HOME": cache, "TWOPHOTON_CONFIG_HOME": config}))
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0xFF202020)
        for index in range(count):
            image.save(str(Path(tmp) / f"shot{index}.png"))
        window = LightTableWindow(Path(tmp))
        stack.callback(window.close)
        return window, Path(tmp)

    def test_a_and_f_mark_the_selection_both_ways(self) -> None:
        from apps.lighttable.app import MARKS_ROLE

        with contextlib.ExitStack() as stack:
            window, folder = self.build(stack)
            tab = window.current_tab()
            tab.sheet.setCurrentRow(0)
            first = tab.current_path()

            tab.toggle_mark("quick")
            self.assertEqual([Path(first)], load_collection("quick"))
            self.assertEqual(("quick",), tab.sheet.item(0).data(MARKS_ROLE))
            self.assertIn("Quick Collection", tab.image_info.text())

            tab.toggle_mark("flagged")
            self.assertEqual({"quick", "flagged"}, set(tab.sheet.item(0).data(MARKS_ROLE)))

            # The same key again takes it back out, and the badge goes with it.
            tab.toggle_mark("quick")
            self.assertEqual([], load_collection("quick"))
            self.assertEqual(("flagged",), tab.sheet.item(0).data(MARKS_ROLE))
            QThreadPool.globalInstance().waitForDone(5000)

    def test_marking_does_not_disturb_the_picture(self) -> None:
        """Flagging is not selecting. Pressing F halfway through a pass must not decode
        the file again and throw away wherever you had zoomed to."""
        with contextlib.ExitStack() as stack:
            window, _folder = self.build(stack)
            tab = window.current_tab()
            tab.sheet.setCurrentRow(0)
            tab.view.zoom(3.0)
            zoomed = tab.view.transform().m11()
            shown = tab.view.item.pixmap().cacheKey()

            tab.toggle_mark("flagged")
            self.assertEqual(zoomed, tab.view.transform().m11())
            self.assertEqual(shown, tab.view.item.pixmap().cacheKey())
            self.assertIn("Flagged", tab.image_info.text())
            QThreadPool.globalInstance().waitForDone(5000)

    def test_marking_says_so_even_in_focus(self) -> None:
        """Focus has no sheet and no sidebar, so the keypress has to leave a receipt."""
        with contextlib.ExitStack() as stack:
            window, _folder = self.build(stack)
            tab = window.current_tab()
            self.assertFalse(tab.pane_visible("gallery"))
            tab.sheet.setCurrentRow(0)
            tab.toggle_mark("flagged")
            self.assertTrue(tab.toast.isVisibleTo(tab))
            self.assertIn("added to Flagged", tab.toast.text())
            tab.toggle_mark("flagged")
            self.assertIn("removed from Flagged", tab.toast.text())
            QThreadPool.globalInstance().waitForDone(5000)

    def test_a_set_opens_in_the_sheet_like_a_folder(self) -> None:
        from apps.lighttable.app import COLLECTION_ROLE

        with contextlib.ExitStack() as stack:
            window, folder = self.build(stack)
            tab = window.current_tab()
            tab.sheet.setCurrentRow(0)
            tab.toggle_mark("flagged")
            tab.sheet.setCurrentRow(1)
            tab.toggle_mark("flagged")

            # The sidebar carries both sets, with what is in them.
            labels = [tab.places.item(row).text() for row in range(tab.places.count())]
            self.assertIn("Quick Collection", labels)
            self.assertTrue(any(text.startswith("Flagged") and "2" in text
                                for text in labels))
            row = next(tab.places.item(index) for index in range(tab.places.count())
                       if tab.places.item(index).data(COLLECTION_ROLE) == "flagged")

            tab.places.itemClicked.emit(row)
            self.assertEqual("flagged", tab.collection)
            self.assertEqual(2, tab.sheet.count())
            self.assertIn("Flagged", tab.folder_info.text())
            self.assertIn("in the order you marked them", tab.folder_info.text())
            # The folder underneath has not moved: it is still where a paste lands.
            self.assertEqual(folder, tab.folder)

            # Unflagging while looking at the flagged set takes it off the sheet.
            tab.sheet.setCurrentRow(0)
            tab.toggle_mark("flagged")
            self.assertEqual(1, tab.sheet.count())

            # And picking a folder again leaves the set.
            tab.set_folder(folder)
            self.assertIsNone(tab.collection)
            self.assertEqual(3, tab.sheet.count())
            QThreadPool.globalInstance().waitForDone(5000)

    def test_a_mark_made_in_one_tab_shows_in_the_other(self) -> None:
        with contextlib.ExitStack() as stack:
            window, folder = self.build(stack)
            first = window.current_tab()
            window.new_tab(folder)
            second = window.current_tab()
            first.sheet.setCurrentRow(0)
            first.toggle_mark("quick")
            self.assertEqual({str(Path(first.current_path()))}, second.marks["quick"])
            QThreadPool.globalInstance().waitForDone(5000)

    def test_the_letter_keys_step_aside_while_you_are_typing(self) -> None:
        """A plain letter reaches a shortcut before the field you are typing in, so
        searching for "a car" must not flag half the shoot."""
        from PyQt6.QtGui import QFocusEvent

        with contextlib.ExitStack() as stack:
            window, _folder = self.build(stack)
            letters = {action.text() for action in window.letter_actions}
            self.assertIn("Flag", letters)
            self.assertIn("Add to Quick Collection", letters)
            self.assertIn("Fit", letters)
            self.assertTrue(all(action.isEnabled() for action in window.letter_actions))

            window.eventFilter(window.search, QFocusEvent(QEvent.Type.FocusIn))
            self.assertFalse(any(action.isEnabled() for action in window.letter_actions))
            window.eventFilter(window.search, QFocusEvent(QEvent.Type.FocusOut))
            self.assertTrue(all(action.isEnabled() for action in window.letter_actions))
            QThreadPool.globalInstance().waitForDone(5000)

    def test_f_is_the_flag_key_and_zoom_moved_to_the_number_row(self) -> None:
        with contextlib.ExitStack() as stack:
            window, _folder = self.build(stack)
            keys = {action.text(): action.shortcut().toString()
                    for menu in window.menuBar().findChildren(QMenu)
                    for action in menu.actions()}
            self.assertEqual("F", keys["Flag"])
            self.assertEqual("A", keys["Add to Quick Collection"])
            self.assertEqual("0", keys["Fit"])
            self.assertEqual("9", keys["Fill"])
            self.assertEqual("1", keys["1:1"])
            QThreadPool.globalInstance().waitForDone(5000)


class PanelSmokeTests(unittest.TestCase):
    """Panels float, dock, resize and are still there next time."""

    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_panels_float_over_a_full_bleed_picture_and_can_be_moved(self) -> None:
        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                window = LightTableWindow(Path(tmp))
                window.resize(1200, 800)
                tab = window.current_tab()
                self.app.processEvents()

                # The viewport is the window, not a third of it.
                self.assertEqual(tab.rect(), tab.view.geometry())

                window.toggle_focus()
                locations = tab.panels["locations"]
                self.assertTrue(locations.isVisibleTo(tab))
                self.assertEqual(tab.panel_area().left(), locations.geometry().left())
                self.assertEqual(tab.panel_area().height(), locations.geometry().height())

                # Drag it into open water: it stops being docked and keeps the geometry.
                locations.state = replace(locations.state, x=400, y=300, snap="free",
                                          width=320, height=240)
                tab.relayout()
                self.assertEqual((400, 300, 320, 240), locations.geometry().getRect())

                # Shove it back against the right edge and it docks there full height.
                locations.state = replace(locations.state,
                                          x=tab.width() - 320, snap="right")
                tab.relayout()
                gallery = tab.panels["gallery"]
                self.assertEqual(tab.width(), locations.geometry().right() + 1)
                # The gallery is already docked there, so the two share the edge.
                self.assertEqual(tab.panel_area().top(), locations.geometry().top())
                self.assertEqual(locations.geometry().bottom() + 1,
                                 gallery.geometry().top())
                self.assertEqual(tab.panel_area().bottom(), gallery.geometry().bottom())

                # Never smaller than you can grab hold of.
                locations.state = replace(locations.state, snap="free", width=10, height=10)
                tab.relayout()
                self.assertGreaterEqual(locations.width(), 120)
                QThreadPool.globalInstance().waitForDone(5000)
                window.close()

    def test_the_arrangement_is_kept_and_can_be_put_back(self) -> None:
        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()
                tab.panels["gallery"].state = replace(
                    tab.panels["gallery"].state, x=120, y=140, snap="free", open=True)
                tab.relayout()
                window.close()

                # A window opened and closed without touching a panel writes nothing;
                # this one was touched, so it left a file.
                self.assertTrue(panels.layout_file().is_file())
                again = LightTableWindow(Path(tmp))
                self.assertEqual("free", again.layout_states["gallery"].snap)
                self.assertEqual(120, again.current_tab().panels["gallery"].state.x)

                again.reset_layout()
                self.assertEqual("right", again.current_tab().panels["gallery"].state.snap)
                QThreadPool.globalInstance().waitForDone(5000)
                again.close()

    def test_closing_the_last_panel_is_the_same_as_focus(self) -> None:
        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()
                window.toggle_focus()
                self.assertFalse(window.focus_action.isChecked())
                tab.panels["gallery"].closed.emit("gallery")
                self.assertFalse(tab.pane_visible("gallery"))
                tab.panels["locations"].closed.emit("locations")
                self.assertTrue(window.focus_action.isChecked())
                QThreadPool.globalInstance().waitForDone(5000)
                window.close()

    def test_the_glass_samples_what_it_is_covering(self) -> None:
        """The proof that the panels are transparent: a capture of the viewport exists,
        and a crop of it comes back at the size of the panel standing on it."""
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                image = QImage(64, 64, QImage.Format.Format_RGB32)
                image.fill(0xFFCC4400)
                image.save(str(Path(tmp) / "orange.png"))
                window = LightTableWindow(Path(tmp))
                window.resize(1200, 800)
                window.show()
                self.app.processEvents()
                tab = window.current_tab()
                window.toggle_focus()
                self.app.processEvents()
                tab._refresh_backdrop()

                self.assertIsNotNone(tab.glass.backdrop.image)
                crop = tab.glass.backdrop.crop(QRect(0, 0, 200, 160))
                self.assertEqual((200, 160), (crop.width(), crop.height()))
                pixels = panels.image_to_rgb(crop)
                self.assertEqual((160, 200, 3), pixels.shape)
                QThreadPool.globalInstance().waitForDone(5000)
                window.close()


if __name__ == "__main__":
    unittest.main()
