import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chuckview.core import (  # noqa: E402
    IMAGE_EXTS,
    filter_paths,
    is_image_path,
    mounted_drives,
    open_path_command,
    pasted_image_name,
    plan_import,
    reveal_command,
    scan_images,
    thumb_key,
    thumb_path_for,
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
        self.assertEqual(["explorer", "/select,\\a\\b.png"], reveal_command("/a/b.png", "win32"))
        linux = reveal_command("/a/b.png", "linux")
        self.assertEqual("dbus-send", linux[0])
        self.assertIn("org.freedesktop.FileManager1.ShowItems", linux)
        if os.name == "posix":  # resolving /a/b.png on a Windows host adds its drive letter
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
            mock.patch("chuckview.core.os.path.ismount",
                       side_effect=lambda path: str(path) in devices),
            mock.patch("chuckview.core.os.stat",
                       side_effect=lambda path: self._Stat(devices[str(path)])),
        )

    def test_mounted_volumes_are_listed_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("citadel", "board_rack"):
                (Path(tmp) / name).mkdir()
            (Path(tmp) / ".hidden").mkdir()
            (Path(tmp) / "notes.txt").touch()
            ismount, stat = self._as_mounts({str(Path(tmp) / "citadel"): 1,
                                            str(Path(tmp) / "board_rack"): 2})
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
            ismount, stat = self._as_mounts({str(Path(run) / "citadel"): 7,
                                            str(Path(mnt) / "citadel"): 7})
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


