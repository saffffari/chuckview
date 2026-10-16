import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent, QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from chuckview import phi  # noqa: E402
from tests.fixtures import FAKE_MRN, build_study  # noqa: E402

APP = QApplication.instance() or QApplication([])


def pump(seconds: float = 0.15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.005)


class WindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["CHUCKVIEW_CONFIG_HOME"] = str(self.tmp / "config")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")
        self.files = build_study(self.tmp / "data")
        from chuckview.app import LibraryWindow

        self.window = LibraryWindow(self.tmp / "set.chuck")
        self.window.resize(1400, 900)
        self.window.show()
        self.addCleanup(self._close)
        plan = self.window.catalog.plan_import(self.tmp / "data")
        self.window._imported(self.window.catalog.commit_import(plan))
        pump()
        self.window.previews.waitForDone(20000)
        pump()

    def _close(self) -> None:
        self.window.prefetcher.wait(5000)
        self.window.close()
        pass

    def select(self, predicate) -> int:
        rows = self.window.grid.model_.rows
        index = next(i for i, row in enumerate(rows) if predicate(row))
        self.window.grid.setCurrentIndex(self.window.grid.model_.index(index))
        pump()
        return rows[index].id

    def test_import_fills_the_grid_with_previews(self):
        model = self.window.grid.model_
        self.assertEqual(5, model.rowCount())
        badges = sorted(row.badge for row in model.rows)
        self.assertEqual(["CT 12", "DX", "DX", "DX", "PNG"], badges)
        store = self.window.store
        self.assertTrue(all(store.has(row.id) for row in model.rows))
        self.assertIn("5 of 5", self.window.status.text())

    def test_search_and_facets_narrow_the_grid(self):
        self.window.filter.search.setText("mod:DX -view:PA")
        self.window.refresh()
        self.assertEqual(2, self.window.grid.model_.rowCount())
        self.window.filter.search.setText("")
        column = self.window.filter.columns[0]  # modality
        self.window.filter.refresh(self.window.catalog, self.window.current_source())
        for row in range(column.values.count()):
            if column.values.item(row).data(Qt.ItemDataRole.UserRole) == "CT":
                column.values.item(row).setSelected(True)
        self.window.refresh()
        self.assertEqual(["CT 12"], [r.badge for r in self.window.grid.model_.rows])

    def test_keys_mark_the_selection(self):
        item_id = self.select(lambda row: row.modality == "DX")
        self.window.flag(1)
        self.window.rate(3)
        self.window.label("green")
        row = self.window.catalog.rows([item_id])[0]
        self.assertEqual((1, 3, "green"), (row.flag, row.rating, row.label))
        self.assertEqual(row, next(r for r in self.window.grid.model_.rows if r.id == item_id))
        self.window.flag(1)  # same key takes it back
        self.assertEqual(0, self.window.catalog.rows([item_id])[0].flag)

    def test_loupe_scrolls_a_series_lazily(self):
        self.select(lambda row: row.frames > 1)
        self.window.show_loupe()
        pump()
        view = self.window.view
        self.assertEqual(12, view.slice_count())
        self.assertEqual(6, view.slice)
        self.assertTrue(view.ruler.isVisible())
        self.assertFalse(self.window.filter.isVisible())
        view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down.value,
                                     Qt.KeyboardModifier.NoModifier))
        self.assertEqual(7, view.slice)
        view.wheelEvent(QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                                    QPoint(0, 120), Qt.MouseButton.NoButton,
                                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                                    False))
        self.assertEqual(6, view.slice)
        view.ruler.scrubbed.emit(0)
        self.assertEqual(0, view.slice)
        self.assertEqual(0, view.shown_slice)  # small slices decode on the spot
        self.assertEqual("soft", view.cycle_preset())
        self.assertEqual((40.0, 400.0), view.window)
        self.window.prefetcher.wait(10000)
        pump()
        self.assertEqual("coronal", view.cycle_plane())
        self.assertEqual(32, view.slice_count())
        self.window.show_grid()
        self.assertTrue(self.window.filter.isVisible())

    def test_window_level_drag_changes_the_window(self):
        self.select(lambda row: row.modality == "DX")
        self.window.show_loupe()
        pump()
        view = self.window.view
        before = view.window
        view.drag_window(30, -10)
        self.assertNotEqual(before, view.window)
        self.assertEqual("custom", view.preset)

    def test_info_panel_tags_notes_and_masked_header(self):
        item_id = self.select(lambda row: row.modality == "DX")
        inspector = self.window.inspector
        inspector.tag_line.setText("include, scoliosis")
        inspector._add_tags()
        inspector.note_edit.setPlainText("good lateral")
        inspector._add_note()
        self.assertEqual(["include", "scoliosis"], self.window.catalog.tags_of(item_id))
        self.assertEqual(1, len(self.window.catalog.notes_of(item_id)))
        row = next(r for r in self.window.grid.model_.rows if r.id == item_id)
        self.assertTrue(row.tagged and row.noted)
        inspector._read_header()
        values = [inspector.header.topLevelItem(i).text(2)
                  for i in range(inspector.header.topLevelItemCount())]
        self.assertNotIn(FAKE_MRN, values)
        self.assertIn(phi.MASK, values)

    def test_offline_original_falls_back_to_preview(self):
        shutil.move(str(self.tmp / "data"), str(self.tmp / "gone"))
        self.window.catalog.refresh_missing()
        self.window.refresh()
        self.assertTrue(all(row.missing for row in self.window.grid.model_.rows))
        self.select(lambda row: row.modality == "DX")
        self.window.show_loupe()
        pump()
        self.assertFalse(self.window.view.item.pixmap().isNull())

    def test_new_collection_and_target(self):
        self.select(lambda row: row.modality == "DX")
        ident = self.window.catalog.create_collection("baseline")
        self.window.target_collection = ident
        self.window.add_to_target()
        names = {name: count for _i, name, _q, count in self.window.catalog.collections()}
        self.assertEqual({"baseline": 1}, names)


class SelfTest(unittest.TestCase):
    def test_self_test_passes(self):
        from chuckview.app import self_test

        old = os.environ.get("CHUCKVIEW_CONFIG_HOME")
        try:
            self.assertEqual(0, self_test())
        finally:
            if old is not None:
                os.environ["CHUCKVIEW_CONFIG_HOME"] = old


if __name__ == "__main__":
    unittest.main()
