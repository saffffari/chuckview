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
from PyQt6.QtWidgets import QMenu, QWidget  # noqa: E402

from apps.lighttable import panels  # noqa: E402
from apps.lighttable.app import COLUMNS, DEFAULT_PANELS  # noqa: E402
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
            save_favorites(["/citadel"], store)
            add_favorite(Path(tmp), store)
            self.assertIn(Path("/citadel"), load_favorites(store))


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
        mounts = [Path("/citadel"), Path("/board_rack")]
        self.assertEqual(
            Path("/citadel"),
            tree_anchor("/citadel/2026/shoot", mounts))

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
                # A folder opens on the folder and the gallery, with no viewer column
                # eating a third of the window for a picture nobody has picked yet.
                self.assertTrue(tab.pane_visible("locations"))
                self.assertTrue(tab.pane_visible("gallery"))
                self.assertFalse(tab.pane_visible("viewer"))
                self.assertFalse(window.focus_mode)
                window.toggle_focus()
                self.assertTrue(tab.pane_visible("viewer"))
                self.assertFalse(tab.pane_visible("locations"))
                self.assertFalse(tab.pane_visible("gallery"))
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

    def test_every_button_is_at_the_top_of_the_sidebar(self) -> None:
        """The bar is in the Locations column, and it is the same bar in every tab."""
        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()

                # The bar and the search box live inside the sidebar, not over the picture.
                sidebar = tab.panels["locations"]
                self.assertIs(sidebar, self.ancestor(window.buttons, sidebar))
                self.assertIs(sidebar, self.ancestor(window.search, sidebar))

                # There is one bar; opening a second tab moves it, it does not clone it.
                window.new_tab(Path(tmp))
                second = window.current_tab()
                self.assertIsNot(second, tab)
                self.assertIs(second.panels["locations"],
                              self.ancestor(window.buttons, second.panels["locations"]))
                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()

    def test_the_pane_buttons_open_and_close_each_column(self) -> None:
        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                window = LightTableWindow(Path(tmp))
                tab = window.current_tab()
                # A folder opens on two columns, and the buttons say so.
                self.assertTrue(window.pane_actions["locations"].isChecked())
                self.assertTrue(window.pane_actions["gallery"].isChecked())
                self.assertFalse(window.pane_actions["viewer"].isChecked())
                self.assertFalse(window.focus_action.isChecked())

                window.pane_actions["viewer"].click()
                self.assertTrue(tab.pane_visible("viewer"))

                # Closing the last two leaves the picture, which is Focus.
                window.pane_actions["gallery"].click()
                window.pane_actions["locations"].click()
                self.assertTrue(window.focus_action.isChecked())
                self.assertTrue(tab.pane_visible("viewer"))

                # And the last column standing cannot be closed into an empty window.
                window.pane_actions["viewer"].click()
                self.assertTrue(tab.pane_visible("viewer"))
                from PyQt6.QtCore import QThreadPool as Pool
                Pool.globalInstance().waitForDone(5000)
                window.close()

    @staticmethod
    def ancestor(widget, target):
        """The first of `widget`'s parents that is `target`, or None."""
        node = widget
        while node is not None:
            if node is target:
                return node
            node = node.parentWidget()
        return None

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
                window.toggle_focus()
                self.assertFalse(tab.pane_visible("gallery"))

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


class ColumnLayoutTests(unittest.TestCase):
    """How wide the columns are, and the one line of text each takes on disk."""

    def test_the_rightmost_open_column_takes_the_leftovers(self) -> None:
        """Sidebars keep the width you gave them; the picture gets the rest of the window.

        This is the whole sizing rule. Without it a splitter shares new space out in
        proportion, and every time you widened the window you would be widening the
        folder tree."""
        states = [PanelState("locations", 260, True), PanelState("gallery", 520, True),
                  PanelState("viewer", 760, True)]
        self.assertEqual([260, 520, 620], panels.column_sizes(states, 1400))

        # With the viewer closed, the gallery is the one that absorbs.
        states[2] = PanelState("viewer", 760, False)
        sizes = panels.column_sizes(states, 1400)
        self.assertEqual(260, sizes[0])
        self.assertEqual(1140, sizes[1])

        # A window too narrow to honour everything still gives every column something
        # you can get hold of, rather than a zero-width column you can never drag back.
        for size in panels.column_sizes(states, 200):
            self.assertGreaterEqual(size, panels.MINIMUM.width())

    def test_a_closed_column_still_has_a_width_to_come_back_to(self) -> None:
        states = [PanelState("locations", 260, False), PanelState("gallery", 520, True),
                  PanelState("viewer", 760, False)]
        sizes = panels.column_sizes(states, 1400)
        self.assertEqual(260, sizes[0])
        self.assertEqual(760, sizes[2])
        self.assertEqual(1400, sizes[1])

    def test_the_layout_is_one_readable_line_per_column(self) -> None:
        states = [PanelState("locations", 300, True), PanelState("gallery", 380, False)]
        text = panels.format_layout(states)
        self.assertIn("locations 300 1", text)
        self.assertIn("gallery 380 0", text)
        parsed = panels.parse_layout(text, DEFAULT_PANELS)
        self.assertEqual(states[0], parsed["locations"])
        self.assertEqual(states[1], parsed["gallery"])
        # A column the file says nothing about keeps its default.
        self.assertEqual("viewer", parsed["viewer"].name)

    def test_a_damaged_layout_falls_back_instead_of_failing(self) -> None:
        defaults = {state.name: state for state in DEFAULT_PANELS}
        text = "# comment\nlocations not-a-number 1\nnonsense 300 1\ngallery 400 1\n"
        parsed = panels.parse_layout(text, DEFAULT_PANELS)
        self.assertEqual(defaults["locations"], parsed["locations"])
        self.assertNotIn("nonsense", parsed)
        self.assertEqual(400, parsed["gallery"].width)

    def test_the_old_floating_layout_file_is_not_read_as_widths(self) -> None:
        """The file this replaces held `name x y width height open snap collapsed`.

        Reading its `x` as the new `width` would open the app with a 26px sidebar clamped
        to the minimum — a layout nobody chose, restored from a file nobody edited. Eight
        fields is not three fields, so the line is skipped and the default stands."""
        old = ("# Light Table panels — name x y width height open snap collapsed\n"
               "locations 26 60 300 560 1 left 0\ngallery 900 60 380 620 1 right 0\n")
        parsed = panels.parse_layout(old, DEFAULT_PANELS)
        self.assertEqual({state.name: state for state in DEFAULT_PANELS}, parsed)

    def test_a_missing_layout_file_is_a_fresh_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = panels.load_layout(DEFAULT_PANELS, Path(tmp) / "never-written.txt")
            self.assertEqual({state.name: state for state in DEFAULT_PANELS}, loaded)

    def test_a_narrow_sidebar_wraps_its_buttons_instead_of_clipping_them(self) -> None:
        """The buttons live in a column the user can make narrow, so the row has to wrap.

        Measured through the layout's own height-for-width, which is what Qt asks: the
        same set of buttons is taller when there is less room for it."""
        from PyQt6.QtWidgets import QApplication, QToolButton

        app = QApplication.instance() or QApplication([])
        holder = QWidget()
        row = panels.FlowRow(holder, spacing=4)
        for text in ("Quick", "Flag", "Copy", "Paste", "Rename", "Path", "Reveal"):
            button = QToolButton(holder)
            button.setText(text)
            row.addWidget(button)
        wide, narrow = row.heightForWidth(2000), row.heightForWidth(150)
        self.assertGreater(narrow, wide)
        self.assertEqual(7, row.count())
        del app


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
            window.toggle_focus()
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


class ColumnSmokeTests(unittest.TestCase):
    """Three columns, in one order, sized but never rearranged — and still there next time."""

    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @contextlib.contextmanager
    def window(self, folder=None):
        from apps.lighttable.app import LightTableWindow

        with contextlib.ExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            cache = stack.enter_context(tempfile.TemporaryDirectory())
            config = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(mock.patch.dict(os.environ, {
                "XDG_CACHE_HOME": cache, "TWOPHOTON_CONFIG_HOME": config}))
            window = LightTableWindow(Path(folder or tmp))
            window.resize(1400, 900)
            window.show()
            self.app.processEvents()
            try:
                yield window, Path(tmp)
            finally:
                QThreadPool.globalInstance().waitForDone(5000)
                window.close()

    def test_the_order_is_locations_gallery_viewer_and_cannot_be_changed(self) -> None:
        with self.window() as (window, _tmp):
            tab = window.current_tab()
            self.assertEqual(("locations", "gallery", "viewer"), COLUMNS)
            on_screen = [tab.splitter.widget(i).name for i in range(tab.splitter.count())]
            self.assertEqual(list(COLUMNS), on_screen)

            # Opening the third column does not reshuffle the first two.
            window.pane_actions["viewer"].click()
            self.app.processEvents()
            self.assertEqual(list(COLUMNS),
                             [tab.splitter.widget(i).name
                              for i in range(tab.splitter.count())])
            # Left to right, each one starts where the last one ended. No overlap, and
            # nothing floating over the picture.
            boxes = [tab.panels[name].geometry() for name in COLUMNS]
            self.assertLess(boxes[0].right(), boxes[1].left())
            self.assertLess(boxes[1].right(), boxes[2].left())

    def test_the_columns_can_be_sized_and_the_widths_are_kept(self) -> None:
        from apps.lighttable.app import LightTableWindow

        with self.window() as (window, tmp):
            tab = window.current_tab()
            window.pane_actions["viewer"].click()
            self.app.processEvents()

            # Drag the seams: a splitter is the only thing that decides widths, so this
            # is what a drag does.
            tab.splitter.setSizes([340, 430, 630])
            tab.splitter.splitterMoved.emit(340, 1)
            self.app.processEvents()
            self.assertEqual(340, tab.panels["locations"].state.width)
            self.assertEqual(430, tab.panels["gallery"].state.width)

            # A wider window is a bigger picture, not a fatter sidebar.
            window.resize(1800, 900)
            self.app.processEvents()
            self.assertEqual(340, tab.splitter.sizes()[0])
            self.assertEqual(430, tab.splitter.sizes()[1])
            self.assertGreater(tab.splitter.sizes()[2], 630)

            config = os.environ["TWOPHOTON_CONFIG_HOME"]
            window.close()
            self.assertTrue(panels.layout_file().is_file())

            with mock.patch.dict(os.environ, {"TWOPHOTON_CONFIG_HOME": config}):
                again = LightTableWindow(tmp)
                self.assertEqual(340, again.layout_states["locations"].width)
                again.reset_layout()
                self.assertEqual(260, again.current_tab().panels["locations"].state.width)
                QThreadPool.globalInstance().waitForDone(5000)
                again.close()

    def test_a_picture_opens_in_focus_and_a_folder_does_not(self) -> None:
        from PyQt6.QtGui import QImage

        from apps.lighttable.app import LightTableWindow

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache, \
                tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache,
                                              "TWOPHOTON_CONFIG_HOME": config}):
                image = QImage(8, 8, QImage.Format.Format_RGB32)
                image.fill(0xFFCC4400)
                shot = Path(tmp) / "shot.png"
                image.save(str(shot))

                # A file is a thing to look at.
                on_image = LightTableWindow(shot)
                tab = on_image.current_tab()
                self.assertTrue(on_image.focus_mode)
                self.assertTrue(tab.pane_visible("viewer"))
                self.assertFalse(tab.pane_visible("locations"))
                self.assertFalse(tab.pane_visible("gallery"))
                self.assertEqual(str(shot.resolve()),
                                 str(Path(tab.view.source_path).resolve()))
                on_image.close()

                # A folder is a thing to look through.
                on_folder = LightTableWindow(Path(tmp))
                tab = on_folder.current_tab()
                self.assertFalse(on_folder.focus_mode)
                self.assertTrue(tab.pane_visible("locations"))
                self.assertTrue(tab.pane_visible("gallery"))
                self.assertFalse(tab.pane_visible("viewer"))

                # Double-clicking a frame is what asks for the picture.
                tab.sheet.setCurrentRow(0)
                tab.open_current()
                self.assertTrue(tab.pane_visible("viewer"))
                # …and it keeps the sheet beside it rather than going full bleed.
                self.assertTrue(tab.pane_visible("gallery"))
                QThreadPool.globalInstance().waitForDone(5000)
                on_folder.close()

    def test_focus_is_the_viewer_alone_and_backs_out_to_browsing(self) -> None:
        with self.window() as (window, _tmp):
            tab = window.current_tab()
            window.toggle_focus()
            self.app.processEvents()
            self.assertEqual({"viewer"},
                             {name for name in COLUMNS if tab.pane_visible(name)})
            # Focus is full bleed: the viewer has no title strip to give the picture a lid.
            self.assertIsNone(tab.panels["viewer"].title)
            self.assertEqual(tab.splitter.width(), tab.panels["viewer"].width())

            window.toggle_focus()
            self.app.processEvents()
            self.assertEqual({"locations", "gallery"},
                             {name for name in COLUMNS if tab.pane_visible(name)})

    def test_the_facts_live_at_the_foot_of_the_sidebar(self) -> None:
        """W is a section of the sidebar, not a fourth column: three columns is the app."""
        with self.window() as (window, _tmp):
            tab = window.current_tab()
            self.assertFalse(tab.pane_visible("info"))
            self.assertNotIn("info", tab.panels)
            window.toggle_details()
            self.app.processEvents()
            self.assertTrue(tab.pane_visible("info"))
            self.assertTrue(window.facts_action.isChecked())
            self.assertEqual(3, tab.splitter.count())
            self.assertTrue(tab.facts.isVisibleTo(tab.panels["locations"]))


if __name__ == "__main__":
    unittest.main()
