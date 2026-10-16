"""The whole DICOM header, for the metadata panel — with everything identifying masked.

The catalog never stores a name, an ID or a date (see dicom.research_fields). The panel is
the one place the full header is shown, read live from the file each time, and even there
the identifying elements are masked until you choose to reveal them for the session. What
counts as identifying follows the HIPAA Safe Harbor list as it maps onto DICOM:

- every person name (VR `PN`) — patient, referring physician, operator, reader;
- every date and time (VR `DA`, `DT`, `TM`) — only a year is not an identifier;
- identifiers, addresses, phone numbers, and the institution and device that could place a
  patient (the PS3.15 Basic Profile attributes below);
- free text written about the patient, which is where a name turns up when it is not in
  the name field;
- every private (odd-group) element: vendors put whatever they like there, PHI included.

UIDs are shown. A pipeline needs them to deduplicate and to tie a series to its study, and
they identify a file, not a person, once the mapping back to the hospital is not held here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from chuckview import dicom

MASK = "••••••"

IDENTIFYING_VR = {"PN", "DA", "DT", "TM"}

IDENTIFYING_KEYWORDS = {
    "PatientID", "OtherPatientIDs", "OtherPatientIDsSequence", "IssuerOfPatientID",
    "PatientBirthDate", "PatientBirthTime", "PatientBirthName", "PatientMotherBirthName",
    "PatientAddress", "PatientTelephoneNumbers", "PatientTelecomInformation",
    "PatientInsurancePlanCodeSequence", "MedicalRecordLocator", "MilitaryRank",
    "BranchOfService", "PatientReligiousPreference", "Occupation", "AdditionalPatientHistory",
    "PatientComments", "PatientState", "EthnicGroup", "CountryOfResidence",
    "RegionOfResidence", "AccessionNumber", "StudyID", "AdmissionID", "ServiceEpisodeID",
    "VisitComments", "RequestAttributesSequence", "RequestedProcedureID",
    "ScheduledProcedureStepID", "PerformedProcedureStepID", "FillerOrderNumberImagingServiceRequest",
    "PlacerOrderNumberImagingServiceRequest", "InstitutionName", "InstitutionAddress",
    "InstitutionalDepartmentName", "InstitutionCodeSequence", "StationName",
    "DeviceSerialNumber", "DetectorID", "PlateID", "CassetteID", "GantryID",
    "ImageComments", "StudyComments", "DerivationDescription", "TextValue",
    "ContentSequence", "ReferencedPatientSequence", "ResponsibleOrganization",
    "ResponsiblePerson", "PersonAddress", "PersonTelephoneNumbers",
    "OriginalAttributesSequence", "ModifiedAttributesSequence",
}


@dataclass(frozen=True)
class Row:
    """One line of the metadata tree."""

    tag: str          # "(0010,0010)"
    keyword: str      # "PatientName"
    name: str         # "Patient's Name"
    vr: str
    value: str        # already masked if identifying and not revealed
    depth: int        # nesting inside sequences
    identifying: bool


def is_identifying(element) -> bool:
    if element.tag.is_private:
        return True
    return element.VR in IDENTIFYING_VR or element.keyword in IDENTIFYING_KEYWORDS


def _value_text(element, limit: int = 160) -> str:
    if element.VR in ("OB", "OW", "OF", "OD", "OL", "OV", "UN"):
        length = len(element.value) if element.value is not None else 0
        return f"<{length:,} bytes>"
    value = element.value
    if type(value).__name__ == "MultiValue" or isinstance(value, (list, tuple)):
        text = "\\".join(str(v) for v in value)
    else:
        text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def header_rows(path: str | os.PathLike, reveal: bool = False) -> list[Row]:
    """Every element of the header, sequences flattened with their depth, pixel data left
    out. Identifying values come back as the mask unless `reveal` — and an identifying
    sequence is masked whole rather than walked, since its children inherit what it is."""
    if not dicom.AVAILABLE:
        return []
    try:
        ds = dicom.pydicom.dcmread(os.fspath(path), stop_before_pixels=True, force=True)
    except Exception:  # noqa: BLE001
        return []
    rows: list[Row] = []

    def walk(dataset, depth: int) -> None:
        for element in dataset:
            if element.tag == 0x7FE00010:  # PixelData
                continue
            identifying = is_identifying(element)
            masked = identifying and not reveal
            tag = f"({element.tag.group:04X},{element.tag.element:04X})"
            if element.VR == "SQ":
                count = len(element.value or [])
                rows.append(Row(tag, element.keyword, element.name, "SQ",
                                MASK if masked else f"{count} item{'s' if count != 1 else ''}",
                                depth, identifying))
                if not masked:
                    for index, child in enumerate(element.value or []):
                        rows.append(Row("", "", f"item {index + 1}", "", "", depth + 1,
                                        identifying))
                        walk(child, depth + 2)
                continue
            rows.append(Row(tag, element.keyword, element.name, element.VR,
                            MASK if masked else _value_text(element), depth, identifying))

    if getattr(ds, "file_meta", None) is not None:
        walk(ds.file_meta, 0)
    walk(ds, 0)
    return rows
