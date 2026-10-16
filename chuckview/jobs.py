"""Background work: imports, previews, the missing-file check.

Every one of these reads or writes the catalog from its own thread on its own SQLite
connection (the catalog keeps one per thread), and talks back to the window only through
signals, so the window never waits on a disk.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from chuckview.catalog import Catalog, ImportPlan
from chuckview.previews import PreviewStore


class Signals(QObject):
    progress = pyqtSignal(str, int, int)       # stage, done, total
    planned = pyqtSignal(object)               # ImportPlan
    imported = pyqtSignal(int)                 # import id
    failed = pyqtSignal(str)
    preview = pyqtSignal(int, bool)            # item id, built
    checked = pyqtSignal(int)                  # items online


class PlanJob(QRunnable):
    def __init__(self, catalog_path: str, folder: str, signals: Signals):
        super().__init__()
        self.catalog_path, self.folder, self.signals = catalog_path, folder, signals
        self.cancelled = False

    def run(self) -> None:
        catalog = Catalog(self.catalog_path)
        try:
            plan = catalog.plan_import(self.folder, self.signals.progress.emit,
                                       lambda: self.cancelled)
            if not self.cancelled:
                self.signals.planned.emit(plan)
        except Exception as error:  # noqa: BLE001 - shown in the dialog, not a crash
            self.signals.failed.emit(str(error))
        finally:
            catalog.close()


class CommitJob(QRunnable):
    def __init__(self, catalog_path: str, plan: ImportPlan, chosen: list[str],
                 signals: Signals):
        super().__init__()
        self.catalog_path, self.plan, self.chosen, self.signals = (
            catalog_path, plan, chosen, signals)

    def run(self) -> None:
        catalog = Catalog(self.catalog_path)
        try:
            import_id = catalog.commit_import(self.plan, self.chosen, self.signals.progress.emit)
            self.signals.imported.emit(import_id)
        except Exception as error:  # noqa: BLE001
            self.signals.failed.emit(str(error))
        finally:
            catalog.close()


class PreviewJob(QRunnable):
    """Build previews for a batch of items, nearest-first as the window asked for them."""

    def __init__(self, catalog_path: str, ids: list[int], signals: Signals):
        super().__init__()
        self.catalog_path, self.ids, self.signals = catalog_path, ids, signals

    def run(self) -> None:
        catalog = Catalog(self.catalog_path)
        store = PreviewStore(catalog.data_dir)
        try:
            for item_id in self.ids:
                if store.has(item_id):
                    self.signals.preview.emit(item_id, True)
                    continue
                path = catalog.key_path(item_id)
                built = bool(path) and store.build(item_id, catalog.viewer_item(item_id), path)
                self.signals.preview.emit(item_id, built)
        finally:
            catalog.close()


class MissingJob(QRunnable):
    def __init__(self, catalog_path: str, signals: Signals):
        super().__init__()
        self.catalog_path, self.signals = catalog_path, signals

    def run(self) -> None:
        catalog = Catalog(self.catalog_path)
        try:
            self.signals.checked.emit(catalog.refresh_missing())
        finally:
            catalog.close()


def pool(threads: int) -> QThreadPool:
    created = QThreadPool()
    created.setMaxThreadCount(threads)
    return created
