"""The catalog: one SQLite file that knows which images a dataset is being built from.

Importing a folder does not copy anything. It walks every subfolder, groups DICOM slices
into series (an X-ray stays its own picture), reads the handful of non-identifying tags a
dataset is selected by, and records where each file is — as a path *relative to the folder
you imported*, so a drive that comes back as `F:` instead of `E:` is one row to fix
(`relocate_root`), not a catalog to rebuild.

What the catalog holds about a person is a keyed hash and nothing else: no name, no MRN,
no birth date, no study date beyond its year, an age capped at 90 (dicom.research_fields).
The secret that keys the hash is generated per catalog, so two catalogs cannot be joined on
it and nobody can recover an MRN by hashing every possible one.

Everything a researcher adds — tags, notes, ratings, pick/reject flags, colour labels,
collections, smart collections — lives here too, next to a `.chuckdata` folder of
previews, so the catalog can be browsed, filtered and labelled with the originals offline.

One person per catalog. SQLite is in WAL mode for a background import and a browsing
window at the same time; it is not made to be opened by two people over a network share.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from chuckview import dicom
from chuckview.core import IMAGE_EXTS, scan_images
from chuckview.query import Compiled, compile_query

SUFFIX = ".chuck"
SCHEMA_VERSION = 1
IMPORT_LIMIT = 2_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, added_at TEXT);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY, root_id INTEGER, folder TEXT, at TEXT, added INTEGER,
    skipped INTEGER, seconds REAL);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    import_id INTEGER, root_id INTEGER NOT NULL, rel TEXT NOT NULL,
    kind TEXT, modality TEXT, series_uid TEXT, study_uid TEXT,
    frames INTEGER, rows INTEGER, cols INTEGER,
    spacing_z REAL, spacing_r REAL, spacing_c REAL, thickness REAL,
    description TEXT, study_description TEXT, protocol TEXT, body TEXT, view TEXT,
    laterality TEXT, manufacturer TEXT, model TEXT, kvp REAL, kernel TEXT, contrast TEXT,
    image_type TEXT, sex TEXT, age INTEGER, year INTEGER, patient TEXT, bits INTEGER,
    transfer TEXT, photometric TEXT, samples INTEGER,
    window_center REAL, window_width REAL, has_window INTEGER DEFAULT 0,
    bytes INTEGER, mtime REAL, added_at TEXT,
    rating INTEGER DEFAULT 0, flag INTEGER DEFAULT 0, label TEXT DEFAULT '',
    missing INTEGER DEFAULT 0, search TEXT,
    UNIQUE (root_id, rel));
CREATE INDEX IF NOT EXISTS items_modality ON items(modality);
CREATE INDEX IF NOT EXISTS items_patient ON items(patient);
CREATE INDEX IF NOT EXISTS items_series ON items(series_uid);
CREATE INDEX IF NOT EXISTS items_import ON items(import_id);
CREATE TABLE IF NOT EXISTS files (
    item_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, root_id INTEGER NOT NULL,
    rel TEXT NOT NULL, frame INTEGER, sop_uid TEXT, slope REAL, intercept REAL,
    depth REAL, size INTEGER, mtime REAL,
    PRIMARY KEY (item_id, ordinal));
CREATE INDEX IF NOT EXISTS files_location ON files(root_id, rel);
CREATE INDEX IF NOT EXISTS files_sop ON files(sop_uid);
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY (item_id, tag_id));
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, author TEXT, at TEXT, body TEXT);
CREATE INDEX IF NOT EXISTS notes_item ON notes(item_id);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, query TEXT,
    created TEXT);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER NOT NULL, item_id INTEGER NOT NULL, position INTEGER,
    PRIMARY KEY (collection_id, item_id));
"""

# The filter columns along the top of the library, and how each one is counted.
FACETS: dict[str, tuple[str, str]] = {
    "modality": ("Modality", "i.modality"),
    "kind": ("Kind", "i.kind"),
    "body": ("Body part", "i.body"),
    "view": ("View", "i.view"),
    "series": ("Series", "i.description"),
    "study": ("Study", "i.study_description"),
    "protocol": ("Protocol", "i.protocol"),
    "manufacturer": ("Manufacturer", "i.manufacturer"),
    "model": ("Model", "i.model"),
    "kernel": ("Kernel", "i.kernel"),
    "thickness": ("Slice mm", "i.thickness"),
    "sex": ("Sex", "i.sex"),
    "year": ("Year", "i.year"),
    "rating": ("Rating", "i.rating"),
    "flag": ("Flag", "i.flag"),
    "label": ("Label", "i.label"),
    "tag": ("Tag", None),
}

LABELS = ("red", "yellow", "green", "blue", "purple")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir_for(catalog_path: str | os.PathLike) -> Path:
    """The previews folder that sits beside a catalog: `cohort.chuck` → `cohort.chuckdata`."""
    path = Path(catalog_path)
    return path.with_name(path.stem + ".chuckdata")


@dataclass
class PlannedItem:
    """One thing an import would add, before anything is written."""

    path: str                     # the key file, absolute
    kind: str
    modality: str = ""
    frames: int = 1
    item: dicom.Item | None = None
    fields: dict = field(default_factory=dict)
    existing: bool = False        # already in the catalog: shown, unticked, not re-added
    size: int = 0
    mtime: float = 0.0

    @property
    def label(self) -> str:
        if self.item is not None:
            return self.item.label
        return Path(self.path).suffix.lstrip(".").upper() or "IMG"


@dataclass
class ImportPlan:
    folder: str
    items: list[PlannedItem]
    files_seen: int = 0
    seconds: float = 0.0

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for planned in self.items:
            key = planned.modality or planned.kind
            tally[key] = tally.get(key, 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: -kv[1]))


@dataclass(frozen=True)
class Row:
    """What the grid needs to draw one cell, and nothing more."""

    id: int
    path: str
    kind: str
    modality: str
    frames: int
    rating: int
    flag: int
    label: str
    missing: bool
    tagged: bool
    noted: bool

    @property
    def badge(self) -> str:
        mod = self.modality or (Path(self.path).suffix.lstrip(".").upper() or "IMG")
        return f"{mod} {self.frames}" if self.frames > 1 else mod


class Catalog:
    """A catalog file. One connection per thread, so an import can write in the background
    while the window reads."""

    def __init__(self, path: str | os.PathLike, create: bool = False):
        self.path = Path(path)
        if not create and not self.path.exists():
            raise FileNotFoundError(self.path)
        self._local = threading.local()
        db = self.db
        with db:
            db.executescript(SCHEMA)
            version = self.meta("schema")
            if version is None:
                self.set_meta("schema", str(SCHEMA_VERSION))
                self.set_meta("created", now())
                self.set_meta("secret", os.urandom(32).hex())
        self.data_dir = data_dir_for(self.path)

    @classmethod
    def create(cls, path: str | os.PathLike) -> Catalog:
        path = Path(path)
        if path.suffix != SUFFIX:
            path = path.with_suffix(SUFFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path, create=True)

    @property
    def db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(str(self.path), timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA foreign_keys=OFF")
            self._local.db = db
        return db

    def close(self) -> None:
        db = getattr(self._local, "db", None)
        if db is not None:
            db.close()
            self._local.db = None

    # -- meta ------------------------------------------------------------------------
    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    @property
    def secret(self) -> bytes:
        return bytes.fromhex(self.meta("secret") or "")

    # -- roots: the folders things were imported from --------------------------------
    def roots(self) -> list[tuple[int, str, bool]]:
        rows = self.db.execute("SELECT id, path FROM roots ORDER BY path").fetchall()
        return [(row["id"], row["path"], os.path.isdir(row["path"])) for row in rows]

    def root_path(self, root_id: int) -> str:
        row = self.db.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()
        return row[0] if row else ""

    def _root_for(self, folder: str) -> tuple[int, str]:
        """The root a folder's files are stored under: an existing root that contains it,
        else the folder itself as a new one."""
        folder = os.path.normpath(os.path.abspath(folder))
        for root_id, path, _online in self.roots():
            try:
                common = os.path.commonpath([os.path.normcase(path), os.path.normcase(folder)])
            except ValueError:  # different drives on Windows
                continue
            if common == os.path.normcase(path):
                return root_id, path
        cursor = self.db.execute("INSERT INTO roots (path, added_at) VALUES (?, ?)",
                                 (folder, now()))
        return cursor.lastrowid, folder

    @staticmethod
    def _rel(path: str, root: str) -> str:
        return PurePosixPath(*Path(os.path.relpath(path, root)).parts).as_posix()

    def absolute(self, root_id: int, rel: str) -> str:
        return os.path.join(self.root_path(root_id), *PurePosixPath(rel).parts)

    def relocate_root(self, root_id: int, new_path: str) -> int:
        """A drive came back under a different letter, or a folder was moved: point the root
        at its new place. Returns how many items are findable there now."""
        new_path = os.path.normpath(os.path.abspath(new_path))
        with self.db:
            self.db.execute("UPDATE roots SET path = ? WHERE id = ?", (new_path, root_id))
        return self.refresh_missing(root_id)

    def refresh_missing(self, root_id: int | None = None) -> int:
        """Mark items whose key file is not where the catalog says. Returns how many are
        online. A stat per item — cheap enough to run on every open, in the background."""
        where, params = ("WHERE root_id = ?", [root_id]) if root_id is not None else ("", [])
        rows = self.db.execute(f"SELECT id, root_id, rel FROM items {where}", params).fetchall()
        roots = {rid: path for rid, path, _ in self.roots()}
        online, changes = 0, []
        for row in rows:
            here = os.path.exists(os.path.join(roots.get(row["root_id"], ""),
                                               *PurePosixPath(row["rel"]).parts))
            online += here
            changes.append((0 if here else 1, row["id"]))
        with self.db:
            self.db.executemany("UPDATE items SET missing = ? WHERE id = ?", changes)
        return online

    # -- import ----------------------------------------------------------------------
    def plan_import(self, folder: str | os.PathLike,
                    progress: Callable[[str, int, int], None] | None = None,
                    cancelled: Callable[[], bool] | None = None) -> ImportPlan:
        """Everything under a folder that could be imported, grouped into items, with what
        is already in the catalog marked. Writes nothing — this is what the import dialog
        shows before you say yes."""
        started = time.perf_counter()
        folder = os.path.normpath(os.path.abspath(os.fspath(folder)))
        report = progress or (lambda stage, done, total: None)
        stop = cancelled or (lambda: False)
        report("scanning", 0, 0)
        found = scan_images(Path(folder), limit=IMPORT_LIMIT)
        paths = [path for _mtime, path in found]
        stats = {path: mtime for mtime, path in found}
        flat = [p for p in paths if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        medical = [p for p in paths if os.path.splitext(p)[1].lower() not in IMAGE_EXTS]
        secret = self.secret
        parsed: dict[str, tuple[dicom.Header, dict]] = {}
        if medical:
            done = 0
            with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
                for path, result in zip(medical, pool.map(
                        lambda p: dicom.read_for_catalog(p, secret), medical, chunksize=32)):
                    done += 1
                    if result is not None:
                        parsed[path] = result
                    if done % 64 == 0 or done == len(medical):
                        report("reading headers", done, len(medical))
                    if stop():
                        break
        known_sops, known_files = self._known()
        planned: list[PlannedItem] = []
        for item in dicom.group_items(header for header, _fields in parsed.values()):
            fields = dict(parsed[item.first.path][1])
            fields["sops"] = {h.path: parsed[h.path][1].get("sop_uid") for h in item.headers}
            sops = list(fields["sops"].values())
            existing = (all(s and s in known_sops for s in sops)
                        or all(os.path.normcase(h.path) in known_files for h in item.headers))
            size = sum(_size(h.path) for h in item.headers)
            planned.append(PlannedItem(path=item.key, kind=item.kind, modality=item.modality,
                                       frames=item.count, item=item, fields=fields,
                                       existing=existing, size=size, mtime=item.mtime))
        for path in flat:
            planned.append(PlannedItem(path=path, kind="image", size=_size(path),
                                       mtime=stats.get(path, 0.0),
                                       existing=os.path.normcase(path) in known_files))
        planned.sort(key=lambda p: (p.path.lower()))
        return ImportPlan(folder=folder, items=planned, files_seen=len(paths),
                          seconds=time.perf_counter() - started)

    def _known(self) -> tuple[set[str], set[str]]:
        sops = {row[0] for row in self.db.execute(
            "SELECT sop_uid FROM files WHERE coalesce(sop_uid, '') != ''")}
        roots = {rid: path for rid, path, _ in self.roots()}
        files = {os.path.normcase(os.path.join(roots.get(rid, ""), *PurePosixPath(rel).parts))
                 for rid, rel in self.db.execute("SELECT root_id, rel FROM files")}
        return sops, files

    def commit_import(self, plan: ImportPlan, chosen: Iterable[str] | None = None,
                      progress: Callable[[str, int, int], None] | None = None) -> int:
        """Write the chosen items of a plan (all new ones if `chosen` is None). Returns the
        import's id; `last_import` is the smart source the library opens on afterwards."""
        report = progress or (lambda stage, done, total: None)
        wanted = set(chosen) if chosen is not None else None
        todo = [p for p in plan.items
                if not p.existing and (wanted is None or p.path in wanted)]
        started = time.perf_counter()
        db = self.db
        with db:
            root_id, root = self._root_for(plan.folder)
            cursor = db.execute(
                "INSERT INTO imports (root_id, folder, at, added, skipped) VALUES (?,?,?,?,?)",
                (root_id, plan.folder, now(), 0, len(plan.items) - len(todo)))
            import_id = cursor.lastrowid
            added = 0
            for index, planned in enumerate(todo):
                if self._insert(planned, import_id, root_id, root):
                    added += 1
                if index % 200 == 0:
                    report("importing", index, len(todo))
            db.execute("UPDATE imports SET added = ?, seconds = ? WHERE id = ?",
                       (added, time.perf_counter() - started, import_id))
        report("importing", len(todo), len(todo))
        return import_id

    def _insert(self, planned: PlannedItem, import_id: int, root_id: int, root: str) -> bool:
        item, fields = planned.item, planned.fields
        rel = self._rel(planned.path, root)
        name = Path(planned.path).name
        if item is not None:
            header = item.first
            z, row_mm, col_mm = item.spacing
            window = header.window
            values = dict(
                kind=item.kind, modality=item.modality, series_uid=header.series,
                study_uid=header.study, frames=item.count, rows=header.rows, cols=header.cols,
                spacing_z=z, spacing_r=row_mm, spacing_c=col_mm,
                thickness=header.thickness or None, description=header.description,
                body=header.body, photometric=header.photometric, samples=header.samples,
                window_center=window[0] if window else None,
                window_width=window[1] if window else None, has_window=1 if window else 0,
                **{key: fields.get(key) for key in (
                    "study_description", "protocol", "view", "laterality", "manufacturer",
                    "model", "kvp", "kernel", "contrast", "image_type", "sex", "age", "year",
                    "patient", "bits", "transfer")})
        else:
            values = dict(kind="image", modality="", frames=1)
            size = _image_size(planned.path)
            if size:
                values.update(cols=size[0], rows=size[1])
        values["search"] = " ".join(str(values.get(key) or "") for key in (
            "modality", "kind", "description", "study_description", "protocol", "body",
            "view", "laterality", "manufacturer", "model", "kernel", "contrast",
            "image_type")).lower() + " " + name.lower()
        values.update(import_id=import_id, root_id=root_id, rel=rel, bytes=planned.size,
                      mtime=planned.mtime, added_at=now())
        columns = ",".join(values)
        marks = ",".join("?" * len(values))
        try:
            cursor = self.db.execute(f"INSERT INTO items ({columns}) VALUES ({marks})",
                                     list(values.values()))
        except sqlite3.IntegrityError:
            return False
        item_id = cursor.lastrowid
        if item is None:
            self.db.execute("INSERT INTO files (item_id, ordinal, root_id, rel, size, mtime) "
                            "VALUES (?,?,?,?,?,?)",
                            (item_id, 0, root_id, rel, planned.size, planned.mtime))
            return True
        rows = []
        by_path = {h.path: h for h in item.headers}
        for ordinal, (path, frame) in enumerate(item.frames):
            header = by_path.get(path, item.first)
            sop = planned.fields.get("sops", {}).get(path) if frame in (None, 0) else None
            rows.append((item_id, ordinal, root_id, self._rel(path, root), frame, sop,
                         header.slope, header.intercept, header.depth(), _size(path), 0.0))
        self.db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        return True

    # -- the viewer's view of an item ------------------------------------------------
    def item(self, item_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT i.*, r.path AS root FROM items i JOIN roots r ON r.id = i.root_id "
            "WHERE i.id = ?", (item_id,)).fetchone()

    def key_path(self, item_id: int) -> str:
        row = self.item(item_id)
        return os.path.join(row["root"], *PurePosixPath(row["rel"]).parts) if row else ""

    def files(self, item_id: int) -> list[str]:
        rows = self.db.execute(
            "SELECT f.rel, r.path FROM files f JOIN roots r ON r.id = f.root_id "
            "WHERE f.item_id = ? ORDER BY f.ordinal", (item_id,)).fetchall()
        return [os.path.join(row["path"], *PurePosixPath(row["rel"]).parts) for row in rows]

    def viewer_item(self, item_id: int) -> dicom.Item | None:
        """Rebuild the viewer's Item from the catalog alone — no header is parsed to open a
        series, which is the difference between opening a 600-slice CT at once and opening
        it after reading 600 files off a network share."""
        row = self.item(item_id)
        if row is None or row["kind"] == "image":
            return None
        files = self.db.execute(
            "SELECT f.rel, f.frame, f.slope, f.intercept, f.depth, r.path FROM files f "
            "JOIN roots r ON r.id = f.root_id WHERE f.item_id = ? ORDER BY f.ordinal",
            (item_id,)).fetchall()
        window = ((row["window_center"], row["window_width"])
                  if row["has_window"] else None)
        spacing = (row["spacing_r"], row["spacing_c"]) if row["spacing_r"] else None
        headers, frames = [], []
        for record in files:
            path = os.path.join(record["path"], *PurePosixPath(record["rel"]).parts)
            frames.append((path, record["frame"]))
            if record["frame"] is None or not headers:
                headers.append(dicom.Header(
                    path=path, series=row["series_uid"] or "", study=row["study_uid"] or "",
                    modality=row["modality"] or "", rows=row["rows"] or 0,
                    cols=row["cols"] or 0, frames=row["frames"] if record["frame"] is not None else 1,
                    description=row["description"] or "", body=row["body"] or "",
                    spacing=spacing, thickness=row["thickness"] or 0.0, window=window,
                    photometric=row["photometric"] or "MONOCHROME2",
                    slope=record["slope"] if record["slope"] is not None else 1.0,
                    intercept=record["intercept"] or 0.0, samples=row["samples"] or 1))
        return dicom.Item(key=self.key_path(item_id), headers=tuple(headers),
                          frames=tuple(frames), mtime=row["mtime"] or 0.0,
                          z_spacing=row["spacing_z"] or 0.0)

    # -- searching -------------------------------------------------------------------
    def _source_clause(self, source: tuple[str, int | None] | None) -> tuple[str, list]:
        """Where in the catalog: everything, one import, one root, one collection."""
        if not source or source[0] == "all":
            return "1", []
        kind, ident = source
        if kind == "import":
            return "i.import_id = ?", [ident]
        if kind == "last_import":
            last = self.db.execute("SELECT max(id) FROM imports").fetchone()[0]
            return "i.import_id = ?", [last or -1]
        if kind == "root":
            return "i.root_id = ?", [ident]
        if kind == "query":  # a fixed search the sidebar offers (Picks, Offline)
            compiled = compile_query(str(ident))
            return compiled.where, compiled.params
        if kind == "folder":  # (root_id, rel prefix) packed as "root_id:prefix"
            root_id, _, prefix = str(ident).partition(":")
            return ("i.root_id = ? AND (i.rel LIKE ? ESCAPE '\\')",
                    [int(root_id), prefix.replace("%", "\\%").replace("_", "\\_") + "/%"])
        if kind == "collection":
            query = self.db.execute("SELECT query FROM collections WHERE id = ?",
                                    (ident,)).fetchone()
            if query and query[0]:
                compiled = compile_query(query[0])
                return compiled.where, compiled.params
            return ("EXISTS (SELECT 1 FROM collection_items c WHERE c.item_id = i.id AND "
                    "c.collection_id = ?)", [ident])
        return "1", []

    def _facet_clause(self, selections: dict[str, Iterable] | None) -> tuple[str, list]:
        clauses, params = [], []
        for name, values in (selections or {}).items():
            values = list(values)
            if not values or name not in FACETS:
                continue
            column = FACETS[name][1]
            if name == "tag":
                marks = ",".join("?" * len(values))
                clauses.append(f"EXISTS (SELECT 1 FROM item_tags t JOIN tags g ON g.id = "
                               f"t.tag_id WHERE t.item_id = i.id AND g.name IN ({marks}))")
                params.extend(values)
                continue
            nulls = [v for v in values if v is None or v == ""]
            real = [v for v in values if not (v is None or v == "")]
            parts = []
            if real:
                parts.append(f"{column} IN ({','.join('?' * len(real))})")
                params.extend(real)
            if nulls:
                parts.append(f"coalesce({column}, '') = ''")
            clauses.append("(" + " OR ".join(parts) + ")")
        return (" AND ".join(clauses) if clauses else "1"), params

    def where(self, query: str = "", source=None, facets=None) -> tuple[str, list, Compiled]:
        compiled = compile_query(query)
        source_sql, source_params = self._source_clause(source)
        facet_sql, facet_params = self._facet_clause(facets)
        sql = f"({source_sql}) AND ({compiled.where}) AND ({facet_sql})"
        return sql, source_params + compiled.params + facet_params, compiled

    ORDERS = {
        "added": "i.id DESC",
        "name": "i.rel COLLATE NOCASE",
        "modality": "i.modality, i.rel COLLATE NOCASE",
        "rating": "i.rating DESC, i.id DESC",
        "frames": "i.frames DESC, i.id DESC",
        "patient": "i.patient, i.year, i.rel COLLATE NOCASE",
        "collection": "i.id",
    }

    def search(self, query: str = "", source=None, facets=None, order: str = "added",
               limit: int | None = None) -> tuple[list[Row], Compiled]:
        sql, params, compiled = self.where(query, source, facets)
        ordering = self.ORDERS.get(order, self.ORDERS["added"])
        if source and source[0] == "collection" and order == "collection":
            ordering = ("(SELECT position FROM collection_items c WHERE c.item_id = i.id "
                        f"AND c.collection_id = {int(source[1])})")
        tail = f" LIMIT {int(limit)}" if limit else ""
        rows = self.db.execute(
            "SELECT i.id, i.rel, r.path AS root, i.kind, i.modality, i.frames, i.rating, "
            "i.flag, i.label, i.missing, "
            "EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id) AS tagged, "
            "EXISTS (SELECT 1 FROM notes n WHERE n.item_id = i.id) AS noted "
            f"FROM items i JOIN roots r ON r.id = i.root_id WHERE {sql} "
            f"ORDER BY {ordering}{tail}", params).fetchall()
        return [Row(id=row["id"],
                    path=os.path.join(row["root"], *PurePosixPath(row["rel"]).parts),
                    kind=row["kind"] or "", modality=row["modality"] or "",
                    frames=row["frames"] or 1, rating=row["rating"] or 0, flag=row["flag"] or 0,
                    label=row["label"] or "", missing=bool(row["missing"]),
                    tagged=bool(row["tagged"]), noted=bool(row["noted"]))
                for row in rows], compiled

    def rows(self, ids: Iterable[int]) -> list[Row]:
        """Fresh grid rows for these items — after a mark, so only those cells redraw."""
        ids = [int(i) for i in ids]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        sql = f"i.id IN ({marks})"
        rows = self.db.execute(
            "SELECT i.id, i.rel, r.path AS root, i.kind, i.modality, i.frames, i.rating, "
            "i.flag, i.label, i.missing, "
            "EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id) AS tagged, "
            "EXISTS (SELECT 1 FROM notes n WHERE n.item_id = i.id) AS noted "
            f"FROM items i JOIN roots r ON r.id = i.root_id WHERE {sql}", ids).fetchall()
        return [Row(id=row["id"],
                    path=os.path.join(row["root"], *PurePosixPath(row["rel"]).parts),
                    kind=row["kind"] or "", modality=row["modality"] or "",
                    frames=row["frames"] or 1, rating=row["rating"] or 0, flag=row["flag"] or 0,
                    label=row["label"] or "", missing=bool(row["missing"]),
                    tagged=bool(row["tagged"]), noted=bool(row["noted"]))
                for row in rows]

    def facet(self, name: str, query: str = "", source=None, facets=None,
              limit: int = 200) -> list[tuple[object, int]]:
        """How many items each value of one facet has, under everything else that is
        selected — the counts are the point: they say what a filter would leave you."""
        others = {k: v for k, v in (facets or {}).items() if k != name}
        sql, params, _compiled = self.where(query, source, others)
        if name == "tag":
            rows = self.db.execute(
                f"SELECT g.name AS value, count(*) AS n FROM items i JOIN roots r ON "
                f"r.id = i.root_id JOIN item_tags t ON t.item_id = i.id JOIN tags g ON "
                f"g.id = t.tag_id WHERE {sql} GROUP BY g.name ORDER BY n DESC LIMIT ?",
                params + [limit]).fetchall()
        else:
            column = FACETS[name][1]
            rows = self.db.execute(
                f"SELECT {column} AS value, count(*) AS n FROM items i JOIN roots r ON "
                f"r.id = i.root_id WHERE {sql} GROUP BY {column} ORDER BY n DESC LIMIT ?",
                params + [limit]).fetchall()
        return [(row["value"], row["n"]) for row in rows]

    def count(self, query: str = "", source=None, facets=None) -> int:
        sql, params, _compiled = self.where(query, source, facets)
        return self.db.execute(f"SELECT count(*) FROM items i JOIN roots r ON "
                               f"r.id = i.root_id WHERE {sql}", params).fetchone()[0]

    # -- marks: rating, flag, label --------------------------------------------------
    def _update(self, ids: Iterable[int], column: str, value) -> None:
        ids = list(ids)
        with self.db:
            self.db.executemany(f"UPDATE items SET {column} = ? WHERE id = ?",
                                [(value, item_id) for item_id in ids])

    def set_rating(self, ids: Iterable[int], stars: int) -> None:
        self._update(ids, "rating", max(0, min(5, int(stars))))

    def set_flag(self, ids: Iterable[int], flag: int) -> None:
        self._update(ids, "flag", max(-1, min(1, int(flag))))

    def toggle_flag(self, ids: Iterable[int], flag: int) -> int:
        """P and X: set the flag, or clear it if every one of them already has it."""
        ids = list(ids)
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        same = self.db.execute(f"SELECT count(*) FROM items WHERE flag = ? AND id IN ({marks})",
                               [flag, *ids]).fetchone()[0]
        value = 0 if same == len(ids) else flag
        self.set_flag(ids, value)
        return value

    def set_label(self, ids: Iterable[int], label: str) -> None:
        self._update(ids, "label", label if label in LABELS else "")

    def toggle_label(self, ids: Iterable[int], label: str) -> str:
        ids = list(ids)
        if not ids:
            return ""
        marks = ",".join("?" * len(ids))
        same = self.db.execute(f"SELECT count(*) FROM items WHERE label = ? AND id IN ({marks})",
                               [label, *ids]).fetchone()[0]
        value = "" if same == len(ids) else label
        self.set_label(ids, value)
        return value

    # -- tags ------------------------------------------------------------------------
    def tag_id(self, name: str) -> int:
        name = name.strip()
        row = self.db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0]
        return self.db.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid

    def add_tags(self, ids: Iterable[int], names: Iterable[str]) -> None:
        ids = list(ids)
        with self.db:
            for name in names:
                if not name.strip():
                    continue
                tag = self.tag_id(name)
                self.db.executemany("INSERT OR IGNORE INTO item_tags VALUES (?, ?)",
                                    [(item_id, tag) for item_id in ids])

    def remove_tags(self, ids: Iterable[int], names: Iterable[str]) -> None:
        ids = list(ids)
        with self.db:
            for name in names:
                row = self.db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
                if row:
                    self.db.executemany("DELETE FROM item_tags WHERE item_id = ? AND tag_id = ?",
                                        [(item_id, row[0]) for item_id in ids])

    def tags_of(self, item_id: int) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT g.name FROM item_tags t JOIN tags g ON g.id = t.tag_id WHERE t.item_id = ? "
            "ORDER BY g.name COLLATE NOCASE", (item_id,))]

    def all_tags(self) -> list[tuple[str, int]]:
        return [(row[0], row[1]) for row in self.db.execute(
            "SELECT g.name, count(t.item_id) FROM tags g LEFT JOIN item_tags t ON "
            "t.tag_id = g.id GROUP BY g.id ORDER BY g.name COLLATE NOCASE")]

    # -- notes -----------------------------------------------------------------------
    def add_note(self, item_id: int, body: str, author: str | None = None) -> int:
        body = body.strip()
        if not body:
            return 0
        with self.db:
            return self.db.execute(
                "INSERT INTO notes (item_id, author, at, body) VALUES (?, ?, ?, ?)",
                (item_id, author or _user(), now(), body)).lastrowid

    def update_note(self, note_id: int, body: str) -> None:
        with self.db:
            if body.strip():
                self.db.execute("UPDATE notes SET body = ?, at = ? WHERE id = ?",
                                (body.strip(), now(), note_id))
            else:
                self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def notes_of(self, item_id: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT id, author, at, body FROM notes WHERE item_id = ? "
                               "ORDER BY id", (item_id,)).fetchall()

    # -- collections -----------------------------------------------------------------
    def create_collection(self, name: str, query: str | None = None) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO collections (name, query, created) VALUES (?, ?, ?)",
                (name.strip(), query or None, now())).lastrowid

    def rename_collection(self, collection_id: int, name: str) -> None:
        with self.db:
            self.db.execute("UPDATE collections SET name = ? WHERE id = ?",
                            (name.strip(), collection_id))

    def delete_collection(self, collection_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM collection_items WHERE collection_id = ?",
                            (collection_id,))
            self.db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    def collections(self) -> list[tuple[int, str, str | None, int]]:
        """(id, name, smart query or None, how many it holds right now)."""
        out = []
        for row in self.db.execute("SELECT id, name, query FROM collections "
                                   "ORDER BY name COLLATE NOCASE"):
            out.append((row["id"], row["name"], row["query"],
                        self.count(source=("collection", row["id"]))))
        return out

    def add_to_collection(self, collection_id: int, ids: Iterable[int]) -> None:
        ids = list(ids)
        start = self.db.execute("SELECT coalesce(max(position), 0) FROM collection_items "
                                "WHERE collection_id = ?", (collection_id,)).fetchone()[0]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO collection_items VALUES (?, ?, ?)",
                                [(collection_id, item_id, start + index + 1)
                                 for index, item_id in enumerate(ids)])

    def remove_from_collection(self, collection_id: int, ids: Iterable[int]) -> None:
        with self.db:
            self.db.executemany("DELETE FROM collection_items WHERE collection_id = ? AND "
                                "item_id = ?", [(collection_id, i) for i in ids])

    def imports(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT id, folder, at, added, skipped FROM imports "
                               "ORDER BY id DESC").fetchall()

    def remove_items(self, ids: Iterable[int]) -> None:
        """Take items out of the catalog. The files are not touched — files are files."""
        ids = [(i,) for i in ids]
        with self.db:
            for table in ("items WHERE id", "files WHERE item_id", "item_tags WHERE item_id",
                          "notes WHERE item_id", "collection_items WHERE item_id"):
                self.db.executemany(f"DELETE FROM {table} = ?", ids)

    # -- export ----------------------------------------------------------------------
    def export_manifest(self, ids: Iterable[int], target: str | os.PathLike,
                        splits: tuple[float, float, float] | None = None,
                        seed: str = "chuckview") -> Path:
        """The selection as a CSV a training script can read: one row per item, every file
        of a series listed, tags and notes along, and — with `splits` — a train/val/test
        column assigned by patient, so no patient's films land on both sides."""
        import csv

        fields = ["id", "key", "kind", "modality", "series_uid", "study_uid", "patient",
                  "frames", "rows", "cols", "spacing_mm", "thickness", "description",
                  "study_description", "body_part", "view", "laterality", "manufacturer",
                  "model", "sex", "age", "year", "rating", "flag", "label", "tags", "notes",
                  "split", "files"]
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item_id in ids:
                row = self.item(item_id)
                if row is None:
                    continue
                group = row["patient"] or row["study_uid"] or f"item-{item_id}"
                writer.writerow({
                    "id": item_id, "key": self.key_path(item_id), "kind": row["kind"],
                    "modality": row["modality"], "series_uid": row["series_uid"],
                    "study_uid": row["study_uid"], "patient": row["patient"],
                    "frames": row["frames"], "rows": row["rows"], "cols": row["cols"],
                    "spacing_mm": " ".join(f"{v:g}" for v in (
                        row["spacing_z"] or 0, row["spacing_r"] or 0, row["spacing_c"] or 0)),
                    "thickness": row["thickness"], "description": row["description"],
                    "study_description": row["study_description"], "body_part": row["body"],
                    "view": row["view"], "laterality": row["laterality"],
                    "manufacturer": row["manufacturer"], "model": row["model"],
                    "sex": row["sex"], "age": row["age"], "year": row["year"],
                    "rating": row["rating"], "flag": row["flag"], "label": row["label"],
                    "tags": ";".join(self.tags_of(item_id)),
                    "notes": " | ".join(n["body"].replace("\n", " ")
                                        for n in self.notes_of(item_id)),
                    "split": split_for(group, splits, seed) if splits else "",
                    "files": ";".join(self.files(item_id)),
                })
        return target


def split_for(group: str, splits: tuple[float, float, float], seed: str) -> str:
    """Deterministic train/val/test for one patient: the same patient always lands in the
    same split for the same seed, whatever else is in the export."""
    digest = hashlib.sha256(f"{seed}|{group}".encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / 2 ** 64
    train, val, _test = splits
    total = sum(splits) or 1.0
    if fraction < train / total:
        return "train"
    if fraction < (train + val) / total:
        return "val"
    return "test"


def _size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _image_size(path: str) -> tuple[int, int] | None:
    try:
        from PyQt6.QtGui import QImageReader
    except ImportError:
        return None
    size = QImageReader(path).size()
    return (size.width(), size.height()) if size.isValid() else None


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return ""
