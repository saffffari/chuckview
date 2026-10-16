import csv
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chuckview import dicom, phi  # noqa: E402
from chuckview.catalog import Catalog, data_dir_for, split_for  # noqa: E402
from chuckview.query import compile_query  # noqa: E402
from tests.fixtures import FAKE_BIRTH, FAKE_MRN, FAKE_NAME, build_study  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")
        self.addCleanup(shutil.rmtree, self.tmp, True)


class DicomCoreTests(Temp):
    def test_sniffing_finds_extensionless_and_skips_junk(self):
        files = build_study(self.tmp / "data")
        self.assertTrue(dicom.is_dicom_path(files["pacs"]))
        self.assertFalse(dicom.is_dicom_path(self.tmp / "data" / "notes" / "readme"))
        self.assertTrue(dicom.might_be_dicom_name("1.2.840.113619.2.55.3.6046.1"))
        self.assertFalse(dicom.might_be_dicom_name("photo.png"))

    def test_series_stack_in_position_order_and_films_stay_apart(self):
        files = build_study(self.tmp / "data")
        headers = [dicom.read_header(p) for p in [*files["ct"], files["ap"], files["lat"],
                                                  files["report"]]]
        self.assertIsNone(headers[-1])  # no pixels, not a picture
        items = dicom.group_items(h for h in headers if h)
        kinds = sorted(item.kind for item in items)
        self.assertEqual(["series", "xray", "xray"], kinds)
        series = next(item for item in items if item.kind == "series")
        self.assertEqual(12, series.count)
        depths = [h.depth() for h in series.headers]
        self.assertEqual(sorted(depths), depths)  # by position, not by InstanceNumber
        self.assertAlmostEqual(2.5, series.spacing[0])

    def test_slice_source_decodes_lazily_and_windows_through_a_table(self):
        files = build_study(self.tmp / "data")
        item = dicom.group_items(dicom.read_header(p) for p in files["ct"])[0]
        decoded = []

        def decoder(path, frame):
            decoded.append(path)
            return dicom.decode_frame(path, frame)

        source = dicom.SliceSource(item, decoder=decoder)
        source.center = 6
        raw = source.get(6)
        self.assertEqual(1, len(decoded))  # one slice to open, not twelve
        self.assertEqual(np.dtype(np.int16), raw.dtype)  # stored raw, not rescaled
        hu = raw.astype(float) + source.rescale(6)[1]
        self.assertGreater(hu.max(), 300)  # rescale makes it Hounsfield
        out = dicom.to_display(raw, *source.rescale(6), 40, 400, False, source.luts)
        self.assertEqual("uint8", str(out.dtype))
        self.assertEqual(255, out.max())
        order = []
        while (index := source.next_wanted()) is not None:
            order.append(index)
            source.get(index)
        self.assertEqual([7, 5, 8, 4], order[:4])  # outward from where you are
        self.assertIsNotNone(source.volume())

    def test_budget_bounds_memory(self):
        files = build_study(self.tmp / "data")
        item = dicom.group_items(dicom.read_header(p) for p in files["ct"])[0]
        source = dicom.SliceSource(item, budget=32 * 32 * 2 * 4)
        source.center = 0
        for index in range(len(source)):
            source.center = index
            source.get(index)
        self.assertLessEqual(source.held(), 4)
        self.assertIsNotNone(source.peek(len(source) - 1))  # never evicts where you are

    def test_metadata_panel_masks_identifiers(self):
        files = build_study(self.tmp / "data")
        rows = {row.keyword: row for row in phi.header_rows(files["ap"])}
        for keyword in ("PatientName", "PatientID", "PatientBirthDate", "AccessionNumber",
                        "InstitutionName", "StudyDate"):
            self.assertEqual(phi.MASK, rows[keyword].value, keyword)
        self.assertEqual("AP", rows["ViewPosition"].value)
        revealed = {row.keyword: row for row in phi.header_rows(files["ap"], reveal=True)}
        self.assertEqual(FAKE_MRN, revealed["PatientID"].value)


class QueryTests(unittest.TestCase):
    def test_terms_compile_to_parameters_never_text(self):
        compiled = compile_query("mod:CT thick:<=1.5 -tag:hardware \"o'brien\" age:10..18")
        self.assertNotIn("o'brien", compiled.where)
        self.assertIn("%o'brien%", compiled.params)
        self.assertIn(1.5, compiled.params)
        self.assertIn(10.0, compiled.params)
        self.assertEqual([], compiled.errors)

    def test_bad_terms_are_reported_not_raised(self):
        compiled = compile_query("age:old is:sideways mod:CT")
        self.assertEqual(2, len(compiled.errors))
        self.assertEqual(["modality:CT"], compiled.terms)


class CatalogTests(Temp):
    def setUp(self) -> None:
        super().setUp()
        self.files = build_study(self.tmp / "data")
        self.catalog = Catalog.create(self.tmp / "set" / "cohort")
        self.addCleanup(self.catalog.close)

    def imported(self) -> int:
        plan = self.catalog.plan_import(self.tmp / "data")
        return self.catalog.commit_import(plan)

    def test_import_groups_and_skips_what_is_not_an_image(self):
        plan = self.catalog.plan_import(self.tmp / "data")
        kinds = sorted(p.kind for p in plan.items)
        self.assertEqual(["image", "series", "xray", "xray", "xray"], kinds)
        self.assertEqual({"DX": 3, "CT": 1, "image": 1}, plan.counts())
        self.catalog.commit_import(plan)
        self.assertEqual(5, self.catalog.count())
        self.assertTrue(data_dir_for(self.catalog.path).name.endswith(".chuckdata"))

    def test_reimport_adds_nothing(self):
        self.imported()
        plan = self.catalog.plan_import(self.tmp / "data")
        self.assertTrue(all(p.existing for p in plan.items))
        self.catalog.commit_import(plan)
        self.assertEqual(5, self.catalog.count())

    def test_catalog_file_holds_no_identifiers(self):
        self.imported()
        self.catalog.add_note(1, "rotated film, fine otherwise")
        self.catalog.close()
        raw = Path(self.catalog.path).read_bytes()
        for wal in Path(self.catalog.path).parent.glob("*-wal"):
            raw += wal.read_bytes()
        for secret in (FAKE_NAME, FAKE_MRN, FAKE_BIRTH, "Fake General Hospital",
                       "ACC-FAKE-1", "20230615"):
            self.assertNotIn(secret.encode(), raw, secret)

    def test_patient_hash_groups_one_person_and_differs_per_catalog(self):
        self.imported()
        hashes = {row[0] for row in self.catalog.db.execute("SELECT patient FROM items "
                                                            "WHERE modality != ''")}
        self.assertEqual(1, len(hashes))
        other = Catalog.create(self.tmp / "other")
        other.commit_import(other.plan_import(self.tmp / "data"))
        theirs = {row[0] for row in other.db.execute("SELECT patient FROM items "
                                                     "WHERE modality != ''")}
        other.close()
        self.assertNotEqual(hashes, theirs)

    def test_search_language_and_facets(self):
        self.imported()
        rows, _ = self.catalog.search("mod:DX view:AP,LAT")
        self.assertEqual(2, len(rows))
        rows, _ = self.catalog.search("bone")  # kernel / description words
        self.assertEqual(["CT"], [r.modality for r in rows])
        rows, _ = self.catalog.search("age:<18 -view:PA")
        self.assertEqual(2, len(rows))
        rows, _ = self.catalog.search("frames:>1")
        self.assertEqual(1, len(rows))
        self.assertEqual("CT 12", rows[0].badge)
        counts = dict(self.catalog.facet("modality"))
        self.assertEqual({"DX": 3, "CT": 1, "": 1}, counts)
        counts = dict(self.catalog.facet("view", facets={"modality": ["DX"]}))
        self.assertEqual({"AP": 1, "LAT": 1, "PA": 1}, counts)

    def test_marks_tags_notes_and_collections(self):
        self.imported()
        rows, _ = self.catalog.search("mod:DX")
        ids = [r.id for r in rows]
        self.catalog.set_rating(ids[:2], 4)
        self.assertEqual(1, self.catalog.toggle_flag(ids, 1))
        self.assertEqual(0, self.catalog.toggle_flag(ids, 1))  # same key takes it back
        self.catalog.add_tags(ids[:1], ["scoliosis", "include"])
        self.catalog.add_note(ids[1], "Hardware at L4 — exclude from baseline")
        self.assertEqual(1, len(self.catalog.search("tag:scoliosis")[0]))
        self.assertEqual(1, len(self.catalog.search("hardware")[0]))  # words reach notes
        self.assertEqual(1, len(self.catalog.search("has:notes")[0]))
        self.assertEqual(2, len(self.catalog.search("rating:>=4")[0]))
        static = self.catalog.create_collection("baseline")
        self.catalog.add_to_collection(static, ids[:2])
        self.catalog.create_collection("young DX", "mod:DX age:<18")
        names = {name: count for _id, name, _q, count in self.catalog.collections()}
        self.assertEqual({"baseline": 2, "young DX": 3}, names)
        self.assertEqual(2, len(self.catalog.search(source=("collection", static))[0]))

    def test_viewer_item_rebuilds_series_without_parsing(self):
        self.imported()
        row = self.catalog.search("is:series")[0][0]
        item = self.catalog.viewer_item(row.id)
        self.assertEqual(12, item.count)
        self.assertEqual(-1024, item.headers[0].intercept)
        source = dicom.SliceSource(item)
        self.assertEqual((32, 32), source.get(0).shape)

    def test_relocating_a_root_finds_moved_files(self):
        self.imported()
        moved = self.tmp / "elsewhere"
        shutil.move(str(self.tmp / "data"), str(moved))
        self.assertEqual(0, self.catalog.refresh_missing())
        self.assertEqual(5, len(self.catalog.search("is:missing")[0]))
        root_id = self.catalog.roots()[0][0]
        self.assertEqual(5, self.catalog.relocate_root(root_id, str(moved)))
        self.assertTrue(Path(self.catalog.files(1)[0]).exists())

    def test_previews_render_beside_the_catalog(self):
        from PyQt6.QtGui import QImage

        from chuckview.previews import PreviewStore

        self.imported()
        store = PreviewStore(self.catalog.data_dir)
        for row in self.catalog.search()[0]:
            item = self.catalog.viewer_item(row.id)
            self.assertTrue(store.build(row.id, item, row.path), row.badge)
            self.assertTrue(store.has(row.id))
        ct = self.catalog.search("is:series")[0][0]
        preview = QImage(str(store.preview_path(ct.id)))
        self.assertEqual((32, 32), (preview.width(), preview.height()))
        film = self.catalog.search("view:AP")[0][0]
        thumb = QImage(str(store.thumb_path(film.id)))
        self.assertEqual(64, thumb.height())  # small originals are not blown up

    def test_manifest_splits_by_patient(self):
        self.imported()
        ids = [r.id for r in self.catalog.search()[0]]
        target = self.catalog.export_manifest(ids, self.tmp / "out" / "set.csv",
                                              splits=(0.7, 0.15, 0.15))
        with open(target, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(5, len(rows))
        dicom_rows = [r for r in rows if r["modality"]]
        self.assertEqual(1, len({r["split"] for r in dicom_rows}))  # one patient, one split
        series = next(r for r in rows if r["kind"] == "series")
        self.assertEqual(12, len(series["files"].split(";")))
        self.assertEqual(split_for("x", (1, 1, 1), "s"), split_for("x", (1, 1, 1), "s"))
        text = target.read_text()
        self.assertNotIn(FAKE_MRN, text)


import numpy as np  # noqa: E402

if __name__ == "__main__":
    unittest.main()
