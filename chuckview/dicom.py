"""DICOM for Light Table: find it, group it, and decode only the slice you are looking at.

Medical images arrive in two shapes and the app shows both as one frame on the sheet:

- **A projection** — an X-ray (CR, DX, an EOS film): one file, one picture. Its own frame,
  even when a whole study shares a series UID, because an AP and a lateral film are two
  pictures and not a two-slice stack.
- **A cross-section series** — a CT or an MR: hundreds of files, one slice each, scattered
  however the exporter felt like (one folder per slice is common). They are grouped by
  series and geometry, ordered along the slice normal, and shown as one stack.

Nothing here decodes a whole volume to open it. A series is a list of frames and a small
cache: the slice on screen is decoded when you get to it, its neighbours are decoded in
the background outward from wherever you are, and the cache stays under a memory budget
sized to the machine. Pixels are kept raw — exactly what the file stores, usually 16 bits —
and rescale and window are folded into one 65 536-entry lookup table, so changing the
window is a table rebuild and an index, not arithmetic over every pixel.

pydicom (and pylibjpeg for compressed transfer syntaxes) is optional. Without it nothing
is recognised as DICOM and the rest of the app does not notice.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

try:  # optional: the viewer still works on ordinary pictures without it
    import pydicom
    from pydicom import config as _pydicom_config
    from pydicom.pixels import pixel_array as _pixel_array

    # Real exports are full of slightly-wrong values (a UID with a leading dot, a date with
    # a stray space). A viewer that refuses those is a viewer that cannot open the archive.
    _pydicom_config.settings.reading_validation_mode = _pydicom_config.IGNORE
    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on installs without pydicom
    pydicom = None
    AVAILABLE = False

DICOM_EXTS = {".dcm", ".dicom", ".dic", ".ima"}

# Projection modalities: every file is its own picture, never a slice of a stack.
PROJECTION = {"CR", "DX", "MG", "XA", "RF", "IO", "PX", "RG", "DF", "OP", "XC"}

# Window presets, in the order L steps through them. "auto" is whatever the file asks for
# (its own window, else a robust range of its pixels); the named ones only mean something
# in Hounsfield units, so only CT gets them.
CT_PRESETS: tuple[tuple[str, tuple[float, float] | None], ...] = (
    ("auto", None),
    ("soft", (40.0, 400.0)),
    ("bone", (400.0, 1800.0)),
    ("lung", (-600.0, 1500.0)),
    ("brain", (40.0, 80.0)),
)
OTHER_PRESETS: tuple[tuple[str, tuple[float, float] | None], ...] = (
    ("auto", None),
    ("full", None),  # min-max of the slice: everything the detector recorded
)

PLANES = ("axial", "coronal", "sagittal")

_HEADER_TAGS = [
    "SeriesInstanceUID", "StudyInstanceUID", "Modality", "Rows", "Columns",
    "NumberOfFrames", "ImagePositionPatient", "ImageOrientationPatient", "InstanceNumber",
    "SeriesDescription", "BodyPartExamined", "PixelSpacing", "ImagerPixelSpacing",
    "SliceThickness", "SpacingBetweenSlices", "WindowCenter", "WindowWidth",
    "PhotometricInterpretation", "RescaleSlope", "RescaleIntercept", "SamplesPerPixel",
    "BitsAllocated", "PixelRepresentation",
]
_HEADER_VERSION = 1


# --- Recognising a DICOM file -----------------------------------------------------------


def might_be_dicom_name(name: str) -> bool:
    """Whether a file name is worth sniffing. PACS exports name files `IM000123`, with no
    extension at all, or after their own UID, which ends in a number that splitext takes for
    an extension (`1.2.840.113619.2.55.3.604688119.968.1`)."""
    ext = os.path.splitext(name)[1].lower()
    return ext in DICOM_EXTS or ext == "" or ext[1:].isdigit()


def sniff(path: str | os.PathLike) -> bool:
    """A Part 10 file says so: `DICM` after a 128-byte preamble. Files with a DICOM extension
    are taken at their word (old ACR-NEMA exports have no preamble); anything else has to
    show the magic, because a home folder is full of extensionless files that are not
    pictures of anyone's spine."""
    if not AVAILABLE:
        return False
    name = os.fspath(path)
    if os.path.splitext(name)[1].lower() in DICOM_EXTS:
        return True
    try:
        with open(name, "rb") as handle:
            head = handle.read(132)
    except OSError:
        return False
    return len(head) == 132 and head[128:132] == b"DICM"


def is_dicom_path(path: str | os.PathLike) -> bool:
    name = os.fspath(path)
    return might_be_dicom_name(os.path.basename(name)) and sniff(name)


# --- Headers, read once and remembered --------------------------------------------------


@dataclass(frozen=True)
class Header:
    """The handful of tags the app needs. Nothing that names or dates a patient is read,
    so nothing that does can end up on screen, in the cache, or in a manifest."""

    path: str
    series: str = ""
    study: str = ""
    modality: str = ""
    rows: int = 0
    cols: int = 0
    frames: int = 1
    position: tuple[float, float, float] | None = None
    orientation: tuple[float, ...] | None = None
    instance: int = 0
    description: str = ""
    body: str = ""
    spacing: tuple[float, float] | None = None  # row, column (mm)
    thickness: float = 0.0
    between: float = 0.0
    window: tuple[float, float] | None = None   # center, width
    photometric: str = "MONOCHROME2"
    slope: float = 1.0
    intercept: float = 0.0
    samples: int = 1

    @property
    def is_image(self) -> bool:
        return self.rows > 0 and self.cols > 0

    @property
    def stackable(self) -> bool:
        """A slice of a volume: it says where it is and which way it faces, and it is not a
        projection that merely happens to carry geometry."""
        return (self.frames == 1 and self.position is not None
                and self.orientation is not None and self.modality not in PROJECTION)

    @property
    def normal(self) -> np.ndarray | None:
        if self.orientation is None or len(self.orientation) != 6:
            return None
        row, col = np.array(self.orientation[:3]), np.array(self.orientation[3:])
        return np.cross(row, col)

    def depth(self) -> float:
        """Where this slice sits along its own normal — the order slices stack in."""
        normal = self.normal
        if normal is None or self.position is None:
            return float(self.instance)
        return float(np.dot(normal, np.array(self.position)))


def _first(value, cast=float):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (list, tuple)) or type(value).__name__ == "MultiValue":
            value = value[0]
        return cast(value)
    except (TypeError, ValueError, IndexError):
        return None


def _floats(value, count: int) -> tuple[float, ...] | None:
    try:
        numbers = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return numbers if len(numbers) == count else None


def read_header(path: str | os.PathLike) -> Header | None:
    """Parse the header only — never the pixels — or None for anything that is not a
    DICOM image (a DICOMDIR, a structured report, a file that only looked like one)."""
    if not AVAILABLE:
        return None
    name = os.fspath(path)
    try:
        ds = pydicom.dcmread(name, stop_before_pixels=True, specific_tags=_HEADER_TAGS,
                             force=os.path.splitext(name)[1].lower() in DICOM_EXTS)
    except Exception:  # noqa: BLE001 - a malformed file is just not a picture
        return None
    rows, cols = _first(ds.get("Rows"), int) or 0, _first(ds.get("Columns"), int) or 0
    if not rows or not cols:
        return None
    center, width = _first(ds.get("WindowCenter")), _first(ds.get("WindowWidth"))
    spacing = _floats(ds.get("PixelSpacing") or ds.get("ImagerPixelSpacing") or (), 2)
    orientation = _floats(ds.get("ImageOrientationPatient") or (), 6)
    return Header(
        path=name,
        series=str(ds.get("SeriesInstanceUID", "") or ""),
        study=str(ds.get("StudyInstanceUID", "") or ""),
        modality=str(ds.get("Modality", "") or "").upper(),
        rows=rows,
        cols=cols,
        frames=max(1, _first(ds.get("NumberOfFrames"), int) or 1),
        position=_floats(ds.get("ImagePositionPatient") or (), 3),
        orientation=tuple(round(v, 4) for v in orientation) if orientation else None,
        instance=_first(ds.get("InstanceNumber"), int) or 0,
        description=str(ds.get("SeriesDescription", "") or "").strip(),
        body=str(ds.get("BodyPartExamined", "") or "").strip(),
        spacing=spacing,
        thickness=_first(ds.get("SliceThickness")) or 0.0,
        between=_first(ds.get("SpacingBetweenSlices")) or 0.0,
        window=(center, width) if center is not None and width and width > 0 else None,
        photometric=str(ds.get("PhotometricInterpretation", "MONOCHROME2") or "MONOCHROME2"),
        slope=_first(ds.get("RescaleSlope")) or 1.0,
        intercept=_first(ds.get("RescaleIntercept")) or 0.0,
        samples=_first(ds.get("SamplesPerPixel"), int) or 1,
    )


def _header_to_row(header: Header) -> str:
    return json.dumps(asdict(header))


def _header_from_row(text: str) -> Header:
    data = json.loads(text)
    for key in ("position", "orientation", "spacing", "window"):
        if data.get(key) is not None:
            data[key] = tuple(data[key])
    return Header(**data)


def header_cache_file() -> Path:
    from chuckview.core import cache_dir  # late: core imports this module
    return cache_dir() / "dicom-headers.sqlite"


class HeaderCache:
    """Headers keyed on path + mtime + size, in one SQLite file in the cache directory.

    This is a cache, not a setting — delete it and the next scan rebuilds it — so it can be
    a database. A CT is hundreds of files and parsing each one is the slow part of opening a
    folder of them, so it happens once per file, ever."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else header_cache_file()
        self._local = threading.local()

    def _db(self) -> sqlite3.Connection | None:
        db = getattr(self._local, "db", None)
        if db is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(str(self.path), timeout=5)
                db.execute("CREATE TABLE IF NOT EXISTS headers (path TEXT PRIMARY KEY, "
                           "mtime REAL, size INTEGER, version INTEGER, series TEXT, data TEXT)")
                db.execute("CREATE INDEX IF NOT EXISTS by_series ON headers(series)")
            except sqlite3.Error:
                return None
            self._local.db = db
        return db

    def lookup(self, stats: dict[str, tuple[float, int]]) -> dict[str, Header | None]:
        """Headers already known for these (path → mtime, size). A cached miss — a file
        known not to be an image — comes back as None; an unknown file is absent."""
        db = self._db()
        if db is None or not stats:
            return {}
        known: dict[str, Header | None] = {}
        names = list(stats)
        for start in range(0, len(names), 900):
            chunk = names[start:start + 900]
            marks = ",".join("?" * len(chunk))
            try:
                rows = db.execute(f"SELECT path, mtime, size, version, data FROM headers "
                                  f"WHERE path IN ({marks})", chunk).fetchall()
            except sqlite3.Error:
                return known
            for path, mtime, size, version, data in rows:
                if version == _HEADER_VERSION and (mtime, size) == stats[path]:
                    known[path] = _header_from_row(data) if data else None
        return known

    def store(self, entries: Iterable[tuple[str, float, int, Header | None]]) -> None:
        db = self._db()
        if db is None:
            return
        rows = [(path, mtime, size, _HEADER_VERSION, header.series if header else "",
                 _header_to_row(header) if header else "")
                for path, mtime, size, header in entries]
        try:
            with db:
                db.executemany("INSERT OR REPLACE INTO headers VALUES (?,?,?,?,?,?)", rows)
        except sqlite3.Error:
            pass

    def series_members(self, series: str) -> list[Header]:
        """Every slice of a series this machine has seen — how a marked series is rebuilt
        from the one path a collection keeps for it."""
        db = self._db()
        if db is None or not series:
            return []
        try:
            rows = db.execute("SELECT data FROM headers WHERE series = ? AND data != ''",
                              (series,)).fetchall()
        except sqlite3.Error:
            return []
        return [_header_from_row(data) for (data,) in rows]


def _stat(path: str) -> tuple[float, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


def read_headers(paths: Iterable[str], cache: HeaderCache | None = None,
                 workers: int | None = None) -> dict[str, Header]:
    """Headers for every path that is a DICOM image, reading only what the cache lacks."""
    cache = cache if cache is not None else HeaderCache()
    stats = {path: stat for path in paths if (stat := _stat(path)) is not None}
    known = cache.lookup(stats)
    missing = [path for path in stats if path not in known]
    if missing:
        workers = workers or min(8, (os.cpu_count() or 2))
        if len(missing) > 16 and workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fresh = list(pool.map(read_header, missing, chunksize=16))
        else:
            fresh = [read_header(path) for path in missing]
        cache.store((path, *stats[path], header) for path, header in zip(missing, fresh))
        known.update(zip(missing, fresh))
    return {path: header for path, header in known.items() if header is not None}


# --- Items: what one frame on the sheet stands for --------------------------------------


@dataclass
class Item:
    """One frame on the contact sheet: an X-ray, a multi-frame file, or a whole series.

    `key` is the path the rest of the app uses for it — the one that gets selected, marked,
    flagged, and written to the collection files. For a series it is the alphabetically
    first of its files, which stays the same however the slices are re-ordered."""

    key: str
    headers: tuple[Header, ...]
    frames: tuple[tuple[str, int | None], ...]  # (file, frame index within it or None)
    mtime: float = 0.0
    z_spacing: float = 0.0

    @property
    def first(self) -> Header:
        return self.headers[0]

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(path for path, _index in self.frames))

    @property
    def count(self) -> int:
        return len(self.frames)

    @property
    def modality(self) -> str:
        return self.first.modality

    @property
    def kind(self) -> str:
        if len(self.headers) > 1:
            return "series"
        if self.count > 1:
            return "frames"
        return "xray" if self.modality in PROJECTION else "slice"

    @property
    def rgb(self) -> bool:
        return self.first.samples == 3

    @property
    def invert(self) -> bool:
        return self.first.photometric == "MONOCHROME1"

    @property
    def spacing(self) -> tuple[float, float, float]:
        """Slice, row and column spacing in mm — 1 wherever the file does not say."""
        row, col = self.first.spacing or (1.0, 1.0)
        z = self.z_spacing or self.first.between or self.first.thickness or 1.0
        return float(z), float(row), float(col)

    @property
    def label(self) -> str:
        """The tag on the thumbnail: what it is and, for a stack, how deep."""
        mod = self.modality or "DCM"
        return f"{mod} {self.count}" if self.count > 1 else mod

    def search_text(self) -> str:
        return " ".join(filter(None, (self.modality, self.first.description, self.first.body,
                                      self.kind))).lower()


def _make_item(headers: list[Header]) -> Item:
    if len(headers) == 1:
        header = headers[0]
        frames = tuple((header.path, i if header.frames > 1 else None)
                       for i in range(header.frames))
        z = header.between or header.thickness
        return Item(key=header.path, headers=(header,), frames=frames,
                    mtime=(_stat(header.path) or (0.0, 0))[0], z_spacing=z)
    ordered = sorted(headers, key=lambda h: (h.depth(), h.instance, h.path))
    depths = np.array([h.depth() for h in ordered])
    steps = np.abs(np.diff(depths))
    steps = steps[steps > 1e-4]
    z = float(np.median(steps)) if steps.size else (ordered[0].between or ordered[0].thickness)
    mtimes = [(_stat(h.path) or (0.0, 0))[0] for h in ordered]
    return Item(key=min(h.path for h in ordered), headers=tuple(ordered),
                frames=tuple((h.path, None) for h in ordered), mtime=max(mtimes),
                z_spacing=z)


def group_items(headers: Iterable[Header]) -> list[Item]:
    """Stack the slices that belong together; leave every other picture on its own.

    Slices stack when they share a series, a matrix size, and an orientation — a scout and
    the axial run in the same series are two things, and so are two reconstructions with
    different matrices."""
    stacks: dict[tuple, list[Header]] = {}
    alone: list[Header] = []
    for header in headers:
        if header.stackable and header.series:
            key = (header.series, header.rows, header.cols, header.orientation)
            stacks.setdefault(key, []).append(header)
        else:
            alone.append(header)
    items = [_make_item([header]) for header in alone]
    items.extend(_make_item(group) for group in stacks.values())
    return items


def collapse(found: list[tuple[float, str]], cache: HeaderCache | None = None
             ) -> tuple[list[tuple[float, str]], dict[str, Item]]:
    """Fold a scan's DICOM files into one entry per item, newest first like the rest.

    Ordinary pictures pass straight through. Every DICOM file becomes part of exactly one
    item, and only the item's key stays in the list."""
    dicom = [path for _mtime, path in found if is_dicom_candidate(path)]
    if not dicom:
        return found, {}
    headers = read_headers(dicom, cache)
    items = {item.key: item for item in group_items(headers.values())}
    dropped = set(dicom)
    kept = [(mtime, path) for mtime, path in found if path not in dropped]
    kept.extend((item.mtime, item.key) for item in items.values())
    kept.sort(reverse=True)
    return kept, items


def is_dicom_candidate(path: str) -> bool:
    """Cheap test for a path the scan already decided is DICOM: by name only."""
    return might_be_dicom_name(os.path.basename(path))


def item_for(path: str, cache: HeaderCache | None = None) -> Item | None:
    """Rebuild the item behind one path — a marked series opened from its collection, or a
    slice handed to the app on the command line — from the headers this machine has seen."""
    cache = cache if cache is not None else HeaderCache()
    headers = read_headers([path], cache)
    header = headers.get(path)
    if header is None:
        return None
    if not header.stackable or not header.series:
        return _make_item([header])
    key = (header.series, header.rows, header.cols, header.orientation)
    members = [h for h in cache.series_members(header.series)
               if (h.series, h.rows, h.cols, h.orientation) == key and os.path.isfile(h.path)]
    if not any(h.path == path for h in members):
        members.append(header)
    return _make_item(members)


# --- Pixels: decode one frame, window it through a table --------------------------------


def decode_frame(path: str, index: int | None = None) -> np.ndarray:
    """One frame's stored values, untouched — no rescale, no window. For a multi-frame
    file only that frame is decoded."""
    if not AVAILABLE:
        raise RuntimeError("pydicom is not installed")
    array = _pixel_array(path, index=index) if index is not None else _pixel_array(path)
    if index is None and array.ndim == 3 and array.shape[-1] not in (3, 4):
        array = array[0]  # a multi-frame file opened as a single picture: its first frame
    return np.ascontiguousarray(array)


def _codes(dtype: np.dtype) -> np.ndarray:
    """Every value a 16- or 8-bit pixel can hold, in the order its bits index a table."""
    if dtype.itemsize == 1:
        return np.arange(256, dtype=np.uint8).view(dtype).astype(np.float64)
    return np.arange(65536, dtype=np.uint16).view(dtype).astype(np.float64)


def window_lut(dtype: np.dtype, slope: float, intercept: float,
               center: float, width: float, invert: bool) -> np.ndarray:
    """The whole display transform as one table: stored value → modality value (rescale)
    → 0–255 (window) → flipped for MONOCHROME1. Building it is 65 536 multiplies; using it
    is one index per pixel, which is what keeps a 9-megapixel film responsive while the
    window is being dragged."""
    values = _codes(np.dtype(dtype)) * slope + intercept
    low = center - width / 2.0
    out = (values - low) * (255.0 / max(width, 1e-6))
    np.clip(out, 0, 255, out=out)
    table = out.astype(np.uint8)
    return 255 - table if invert else table


def to_display(raw: np.ndarray, slope: float, intercept: float, center: float,
               width: float, invert: bool, _luts: dict | None = None) -> np.ndarray:
    """Stored pixels to 8-bit grey (or RGB passed through) for the screen."""
    if raw.ndim == 3 and raw.shape[-1] in (3, 4):
        return np.ascontiguousarray(raw[..., :3].astype(np.uint8, copy=False))
    if raw.dtype.kind in "iu" and raw.dtype.itemsize <= 2:
        key = (raw.dtype.str, slope, intercept, center, width, invert)
        table = _luts.get(key) if _luts is not None else None
        if table is None:
            table = window_lut(raw.dtype, slope, intercept, center, width, invert)
            if _luts is not None:
                if len(_luts) > 16:
                    _luts.clear()
                _luts[key] = table
        index = raw.view(np.uint8 if raw.dtype.itemsize == 1 else np.uint16)
        return table[index]
    values = raw.astype(np.float32) * slope + intercept
    out = (values - (center - width / 2.0)) * (255.0 / max(width, 1e-6))
    np.clip(out, 0, 255, out=out)
    table = out.astype(np.uint8)
    return 255 - table if invert else table


def auto_window(raw: np.ndarray, slope: float, intercept: float) -> tuple[float, float]:
    """A window from the pixels themselves: the 0.5–99.5 percentile range of a sparse
    sample, so a burned-in marker or a collimator edge does not flatten the picture."""
    sample = raw[:: max(1, raw.shape[0] // 256), :: max(1, raw.shape[1] // 256)]
    low, high = np.percentile(sample.astype(np.float64) * slope + intercept, (0.5, 99.5))
    if high <= low:
        high = low + 1.0
    return (low + high) / 2.0, high - low


def full_window(raw: np.ndarray, slope: float, intercept: float) -> tuple[float, float]:
    low = float(raw.min()) * slope + intercept
    high = float(raw.max()) * slope + intercept
    if high <= low:
        high = low + 1.0
    return (low + high) / 2.0, high - low


def presets_for(item: Item) -> tuple[tuple[str, tuple[float, float] | None], ...]:
    return CT_PRESETS if item.modality == "CT" else OTHER_PRESETS


# --- The slice source: decode on demand, prefetch outward, stay under budget ------------


def memory_budget() -> int:
    """How much decoded pixel data one open item may hold: a quarter of the machine's RAM,
    between 256 MB and 2 GB. A hospital laptop with 8 GB gets a bounded cache rather than a
    swap storm; HAL gets a whole CT in memory."""
    total = _physical_memory()
    budget = total // 4 if total else 1 << 30
    return int(min(max(budget, 256 << 20), 2 << 30))


def _physical_memory() -> int:
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong),
                            ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended", ctypes.c_ulonglong)]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total)
            return 0
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return 0


@dataclass
class SliceSource:
    """The frames of one item, decoded only as they are needed.

    `get` decodes the frame you asked for if it is not already held. `next_wanted` is what a
    background thread should decode next: the nearest missing frame to `center`, which the
    viewer moves as you scroll, so prefetch always works outward from where you are rather
    than from wherever it happened to start. The cache evicts the frame furthest from the
    centre when it is over budget, never the one on screen."""

    item: Item
    budget: int = field(default_factory=memory_budget)
    decoder: Callable[[str, int | None], np.ndarray] = decode_frame
    center: int = 0

    def __post_init__(self) -> None:
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()
        self._bytes = 0
        self._failed: set[int] = set()
        self.luts: dict = {}

    def __len__(self) -> int:
        return self.item.count

    @property
    def frame_bytes(self) -> int:
        header = self.item.first
        return header.rows * header.cols * max(1, header.samples) * 2

    @property
    def capacity(self) -> int:
        """How many frames fit in the budget — at least the one on screen and its two
        neighbours, whatever the budget says."""
        return max(3, self.budget // max(1, self.frame_bytes))

    @property
    def fits(self) -> bool:
        return self.capacity >= len(self)

    def rescale(self, index: int) -> tuple[float, float]:
        headers = self.item.headers
        header = headers[index] if len(headers) == len(self) else headers[0]
        return header.slope, header.intercept

    def peek(self, index: int) -> np.ndarray | None:
        with self._lock:
            return self._cache.get(index)

    def held(self) -> int:
        with self._lock:
            return len(self._cache)

    def get(self, index: int) -> np.ndarray | None:
        """The frame's stored pixels, decoding it now if it is not held. None if it cannot
        be decoded (a missing codec, a truncated file) — a gap, not a crash."""
        if not 0 <= index < len(self):
            return None
        cached = self.peek(index)
        if cached is not None or index in self._failed:
            return cached
        path, frame = self.item.frames[index]
        try:
            array = self.decoder(path, frame)
        except Exception:  # noqa: BLE001 - one bad slice must not take the series with it
            self._failed.add(index)
            return None
        self._put(index, array)
        return array

    def _put(self, index: int, array: np.ndarray) -> None:
        with self._lock:
            if index in self._cache:
                return
            self._cache[index] = array
            self._bytes += array.nbytes
            while self._bytes > self.budget and len(self._cache) > 1:
                far = max(self._cache, key=lambda i: abs(i - self.center))
                if far == index:
                    break
                self._bytes -= self._cache.pop(far).nbytes

    def next_wanted(self) -> int | None:
        """The nearest frame to the centre that is neither held nor known to be broken,
        within as many frames as the budget can hold. None when there is nothing left to do."""
        reach = self.capacity // 2 if not self.fits else len(self)
        with self._lock:
            held = set(self._cache)
        for distance in range(0, len(self)):
            if distance > reach:
                return None
            for index in (self.center + distance, self.center - distance):
                if 0 <= index < len(self) and index not in held and index not in self._failed:
                    return index
        return None

    def complete(self) -> bool:
        with self._lock:
            return len(self._cache) + len(self._failed) >= len(self)

    def volume(self) -> np.ndarray | None:
        """The whole stack as one array, for the coronal and sagittal planes — only once
        every slice is held and they all share a rescale, else None."""
        if len(self) < 2 or not self.complete() or self._failed or self.item.rgb:
            return None
        if len({self.rescale(i) for i in range(len(self))}) > 1:
            return None
        with self._lock:
            slices = [self._cache[i] for i in range(len(self))]
        if len({(s.shape, s.dtype) for s in slices}) > 1:
            return None
        return np.stack(slices)


def plane_slice(volume: np.ndarray, plane: str, index: int) -> np.ndarray:
    """One cut through a stack, superior at the top. Slices are stored in ascending depth
    along the normal; for an axial CT in patient coordinates that is foot to head, so the
    reformatted planes are flipped to put the head where a reader expects it."""
    if plane == "coronal":
        return np.ascontiguousarray(volume[::-1, index, :])
    if plane == "sagittal":
        return np.ascontiguousarray(volume[::-1, :, index])
    return volume[index]


def plane_depth(volume_shape: tuple[int, ...], plane: str) -> int:
    """How many cuts there are through the stack in this plane."""
    count, rows, cols = volume_shape[:3]
    return {"axial": count, "coronal": rows, "sagittal": cols}[plane]


def plane_aspect(item: Item, plane: str) -> tuple[float, float]:
    """Screen scale (x, y) that makes a pixel in this plane as wide and tall as the tissue
    it shows. The smaller axis is 1, so the picture only ever grows to be honest."""
    z, row, col = item.spacing
    x, y = {"axial": (col, row), "coronal": (col, z), "sagittal": (row, z)}[plane]
    small = min(x, y) or 1.0
    return x / small, y / small


# --- Manifest: the selection, as a file a training script can read ----------------------

MANIFEST_FIELDS = ("key", "kind", "modality", "series_uid", "study_uid", "frames", "rows",
                   "cols", "spacing_mm", "description", "body_part", "files")


def manifest_row(path: str, item: Item | None) -> dict[str, str]:
    """One line of the manifest. Identifiers are the UIDs a pipeline needs to deduplicate
    and split by study — never a name, an MRN, or a date."""
    if item is None:
        return {"key": path, "kind": "image", "frames": "1", "files": path}
    z, row, col = item.spacing
    return {
        "key": item.key,
        "kind": item.kind,
        "modality": item.modality,
        "series_uid": item.first.series,
        "study_uid": item.first.study,
        "frames": str(item.count),
        "rows": str(item.first.rows),
        "cols": str(item.first.cols),
        "spacing_mm": f"{z:g} {row:g} {col:g}",
        "description": item.first.description,
        "body_part": item.first.body,
        "files": ";".join(item.files),
    }


def write_manifest(rows: Iterable[dict[str, str]], target: str | os.PathLike) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return target


def decoder_report() -> dict[str, bool]:
    """Which compressed transfer syntaxes this install can open — the thing a frozen
    Windows build most easily gets wrong, so `--self-test` prints it."""
    if not AVAILABLE:
        return {}
    from pydicom import uid
    from pydicom.pixels import get_decoder

    report = {}
    for name in ("ExplicitVRLittleEndian", "RLELossless", "JPEGBaseline8Bit",
                 "JPEGLosslessSV1", "JPEGLossless", "JPEG2000Lossless", "JPEG2000",
                 "JPEGLSLossless"):
        try:
            report[name] = bool(get_decoder(getattr(uid, name)).is_available)
        except Exception:  # noqa: BLE001
            report[name] = False
    return report
