"""Synthetic DICOM for the tests. Nothing here came from a person: every value is made up,
including the identifiers, which exist only to prove they never reach the catalog."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage, DigitalXRayImageStorageForPresentation, ExplicitVRLittleEndian,
    RLELossless, generate_uid,
)

FAKE_NAME = "Testperson^Fakey"
FAKE_MRN = "MRN-0042-FAKE"
FAKE_BIRTH = "19800101"


def _base(sop_class, modality: str, series: str, study: str, rows: int, cols: int) -> Dataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = modality
    ds.SeriesInstanceUID = series
    ds.StudyInstanceUID = study
    ds.PatientName = FAKE_NAME
    ds.PatientID = FAKE_MRN
    ds.PatientBirthDate = FAKE_BIRTH
    ds.PatientSex = "F"
    ds.StudyDate = "20230615"
    ds.AccessionNumber = "ACC-FAKE-1"
    ds.InstitutionName = "Fake General Hospital"
    ds.Manufacturer = "FakeVendor"
    ds.Rows, ds.Columns = rows, cols
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    return ds


def write_ct_series(folder: Path, count: int = 12, rows: int = 32, cols: int = 32,
                    one_folder_per_slice: bool = True, compress: bool = True,
                    study: str | None = None) -> list[Path]:
    """A CT whose slices are written shuffled, one per folder like the real exports, with
    HU rescale and a body that is a bright cylinder so windowing has something to show."""
    series, study = generate_uid(), study or generate_uid()
    order = list(range(count))
    random.Random(7).shuffle(order)
    paths = []
    yy, xx = np.mgrid[0:rows, 0:cols]
    disc = ((yy - rows / 2) ** 2 + (xx - cols / 2) ** 2) < (rows / 3) ** 2
    for written, z in enumerate(order):
        ds = _base(CTImageStorage, "CT", series, study, rows, cols)
        ds.PixelRepresentation = 1
        ds.ImagePositionPatient = [0.0, 0.0, float(z) * 2.5]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [0.5, 0.5]
        ds.SliceThickness = 2.5
        ds.InstanceNumber = count - z  # deliberately disagrees with position
        ds.RescaleSlope = 1
        ds.RescaleIntercept = -1024
        ds.WindowCenter = 40
        ds.WindowWidth = 400
        ds.SeriesDescription = "AX SPINE BONE"
        ds.BodyPartExamined = "SPINE"
        ds.ConvolutionKernel = "BONEPLUS"
        pixels = np.where(disc, 1024 + 400 + z, 0).astype(np.int16)
        ds.PixelData = pixels.tobytes()
        if compress:
            ds.compress(RLELossless, pixels)
        target = (folder / f"slice-{written:03d}" / "artifact.dcm" if one_folder_per_slice
                  else folder / f"IM{written:05d}.dcm")
        target.parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(target, enforce_file_format=True)
        paths.append(target)
    return paths


def write_xray(path: Path, view: str, series: str, study: str, rows: int = 64,
               cols: int = 24, name_only: bool = False) -> Path:
    """A DX film. `name_only` writes it with no extension, as PACS exports do."""
    ds = _base(DigitalXRayImageStorageForPresentation, "DX", series, study, rows, cols)
    ds.PixelRepresentation = 0
    ds.ImagerPixelSpacing = [0.179363, 0.179363]
    ds.ViewPosition = view
    ds.WindowCenter = 2000
    ds.WindowWidth = 4000
    ds.SeriesDescription = f"SPINE {view}"
    ds.BodyPartExamined = "SPINE"
    ds.PatientAge = "014Y"
    ds.KVP = 85
    ds.PixelData = (np.arange(rows * cols, dtype=np.uint16).reshape(rows, cols) * 3).tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    if name_only:
        path = path.with_suffix("")
    ds.save_as(path, enforce_file_format=True)
    return path


def write_not_an_image(path: Path) -> Path:
    """A Part 10 file with no pixels (like a DICOMDIR or a report)."""
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.88.11"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = "SR"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)
    return path


def build_study(root: Path) -> dict[str, object]:
    """The canonical mixed folder: a CT, an AP+LAT pair, a UID-named film, junk, a PNG."""
    from PyQt6.QtGui import QColor, QImage

    study = generate_uid()
    ct = write_ct_series(root / "ct", study=study)
    films_series = generate_uid()
    ap = write_xray(root / "films" / "ap.dcm", "AP", films_series, study)
    lat = write_xray(root / "films" / "lat.dcm", "LAT", films_series, study)
    pacs = write_xray(root / "pacs" / "IM000001.dcm", "PA", generate_uid(), study,
                      name_only=True)
    report = write_not_an_image(root / "films" / "report.dcm")
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "notes" / "readme").write_text("not a picture of anyone's spine\n")
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    photo = root / "photos" / "setup.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(photo))
    return {"ct": ct, "ap": ap, "lat": lat, "pacs": pacs, "report": report, "photo": photo}
