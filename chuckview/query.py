"""The search language: one line that says which images you want.

    mod:CT thick:<=1.5 body:spine tag:scoliosis -tag:hardware "sag"

- **Words** match anywhere in an item's text — its descriptions, protocol, body part,
  manufacturer, folder and file name — and its notes. All of them have to match.
- **field:value** narrows one field. Text fields match if they *contain* the value;
  `field:=value` matches exactly. Commas mean any of: `mod:CT,MR`, `view:AP,PA`.
- **Numbers** take comparisons and ranges: `age:>=12`, `thick:<2`, `year:2019..2023`,
  `frames:>1`.
- **-** in front of a word or a term negates it: `-tag:reviewed`, `-lateral`.
- **is:** and **has:** test state: `is:pick`, `is:reject`, `is:unflagged`, `is:missing`,
  `is:series`, `is:xray`, `has:notes`, `has:tags`, `has:patient`.
- **OR** between two terms makes either one enough: `body:spine OR body:pelvis`.

Everything compiles to one parameterised SQL WHERE clause over the catalog's items table;
nothing typed is ever pasted into SQL.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

# field name (and its aliases) → (column or expression, type)
TEXT, NUMBER, SPECIAL = "text", "number", "special"
FIELDS: dict[str, tuple[str, str]] = {
    "modality": ("i.modality", TEXT),
    "kind": ("i.kind", TEXT),
    "body": ("i.body", TEXT),
    "series": ("i.description", TEXT),
    "study": ("i.study_description", TEXT),
    "protocol": ("i.protocol", TEXT),
    "view": ("i.view", TEXT),
    "laterality": ("i.laterality", TEXT),
    "manufacturer": ("i.manufacturer", TEXT),
    "model": ("i.model", TEXT),
    "kernel": ("i.kernel", TEXT),
    "contrast": ("i.contrast", TEXT),
    "imagetype": ("i.image_type", TEXT),
    "sex": ("i.sex", TEXT),
    "transfer": ("i.transfer", TEXT),
    "label": ("i.label", TEXT),
    "path": ("(r.path || '/' || i.rel)", TEXT),
    "patient": ("i.patient", TEXT),
    "series_uid": ("i.series_uid", TEXT),
    "study_uid": ("i.study_uid", TEXT),
    "age": ("i.age", NUMBER),
    "year": ("i.year", NUMBER),
    "kvp": ("i.kvp", NUMBER),
    "thickness": ("i.thickness", NUMBER),
    "spacing": ("i.spacing_c", NUMBER),
    "zspacing": ("i.spacing_z", NUMBER),
    "frames": ("i.frames", NUMBER),
    "rows": ("i.rows", NUMBER),
    "cols": ("i.cols", NUMBER),
    "bits": ("i.bits", NUMBER),
    "rating": ("i.rating", NUMBER),
    "import": ("i.import_id", NUMBER),
    "tag": ("tag", SPECIAL),
    "note": ("note", SPECIAL),
    "collection": ("collection", SPECIAL),
    "is": ("is", SPECIAL),
    "has": ("has", SPECIAL),
}
ALIASES = {
    "mod": "modality", "m": "modality", "type": "kind", "part": "body", "bodypart": "body",
    "desc": "series", "description": "series", "ser": "series", "proto": "protocol",
    "lat": "laterality", "side": "laterality", "mfr": "manufacturer", "vendor": "manufacturer",
    "make": "manufacturer", "thick": "thickness", "slice": "thickness", "px": "spacing",
    "pixel": "spacing", "z": "zspacing", "slices": "frames", "n": "frames",
    "stars": "rating", "tags": "tag", "notes": "note", "in": "collection", "folder": "path",
    "file": "path", "color": "label", "colour": "label", "pt": "patient",
}

# What a bare word searches: every text field a person would think of, plus the path.
TEXT_SEARCH = ("lower(coalesce(i.search, '') || ' ' || r.path || '/' || i.rel)")

_COMPARE = re.compile(r"^(<=|>=|<|>|=)?\s*(-?\d+(?:\.\d+)?)$")
_RANGE = re.compile(r"^(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)$")


class QueryError(ValueError):
    """A term that cannot mean anything — shown under the search line, not raised at you."""


@dataclass
class Compiled:
    where: str = "1"
    params: list = field(default_factory=list)
    terms: list[str] = field(default_factory=list)  # what was understood, for the readout
    errors: list[str] = field(default_factory=list)


def resolve(name: str) -> str | None:
    name = name.lower()
    name = ALIASES.get(name, name)
    return name if name in FIELDS else None


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:  # an unclosed quote: take the rest literally
        return text.replace('"', " ").split()


def _number_clause(column: str, value: str) -> tuple[str, list]:
    match = _RANGE.match(value)
    if match:
        return f"({column} BETWEEN ? AND ?)", [float(match[1]), float(match[2])]
    match = _COMPARE.match(value)
    if not match:
        raise QueryError(f"not a number: {value}")
    op = match[1] or "="
    return f"({column} {op} ?)", [float(match[2])]


def _text_clause(column: str, value: str) -> tuple[str, list]:
    exact = value.startswith("=")
    values = [v for v in (value[1:] if exact else value).split(",") if v]
    if not values:
        raise QueryError("empty value")
    if exact:
        marks = ",".join("?" * len(values))
        return f"(lower({column}) IN ({marks}))", [v.lower() for v in values]
    parts = " OR ".join(f"lower(coalesce({column},'')) LIKE ? ESCAPE '\\'" for _ in values)
    return f"({parts})", [f"%{_escape(v.lower())}%" for v in values]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_STATE = {
    "pick": "i.flag = 1", "picked": "i.flag = 1", "flagged": "i.flag = 1",
    "reject": "i.flag = -1", "rejected": "i.flag = -1",
    "unflagged": "i.flag = 0",
    "missing": "i.missing = 1", "online": "i.missing = 0",
    "series": "i.kind = 'series'", "xray": "i.kind = 'xray'", "image": "i.kind = 'image'",
    "stack": "i.frames > 1", "rated": "i.rating > 0", "unrated": "i.rating = 0",
    "labeled": "coalesce(i.label, '') != ''",
}
_HAS = {
    "notes": "EXISTS (SELECT 1 FROM notes n WHERE n.item_id = i.id)",
    "note": "EXISTS (SELECT 1 FROM notes n WHERE n.item_id = i.id)",
    "tags": "EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id)",
    "tag": "EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id)",
    "patient": "coalesce(i.patient, '') != ''",
    "label": "coalesce(i.label, '') != ''",
    "window": "i.has_window = 1",
}


def _special_clause(name: str, value: str) -> tuple[str, list]:
    if name == "is":
        clause = _STATE.get(value.lower())
        if clause is None:
            raise QueryError(f"is:{value}? try is:pick, is:reject, is:missing, is:series")
        return f"({clause})", []
    if name == "has":
        clause = _HAS.get(value.lower())
        if clause is None:
            raise QueryError(f"has:{value}? try has:notes, has:tags, has:patient")
        return f"({clause})", []
    if name == "tag":
        values = [v.lower() for v in value.split(",") if v]
        parts = " OR ".join("lower(g.name) = ?" for _ in values)
        return (f"(EXISTS (SELECT 1 FROM item_tags t JOIN tags g ON g.id = t.tag_id "
                f"WHERE t.item_id = i.id AND ({parts})))", values)
    if name == "note":
        return ("(EXISTS (SELECT 1 FROM notes n WHERE n.item_id = i.id AND "
                "lower(n.body) LIKE ? ESCAPE '\\'))", [f"%{_escape(value.lower())}%"])
    if name == "collection":
        return ("(EXISTS (SELECT 1 FROM collection_items c JOIN collections k ON "
                "k.id = c.collection_id WHERE c.item_id = i.id AND lower(k.name) = ?))",
                [value.lower()])
    raise QueryError(f"unknown field {name}")


def _word_clause(word: str) -> tuple[str, list]:
    like = f"%{_escape(word.lower())}%"
    return (f"({TEXT_SEARCH} LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM notes n WHERE "
            f"n.item_id = i.id AND lower(n.body) LIKE ? ESCAPE '\\'))", [like, like])


def term_clause(token: str) -> tuple[str, list, str]:
    """One token to (sql, params, description)."""
    negate = token.startswith("-") and len(token) > 1
    body = token[1:] if negate else token
    name, colon, value = body.partition(":")
    if colon and value and resolve(name):
        field_name = resolve(name)
        column, kind = FIELDS[field_name]
        if kind == NUMBER:
            sql, params = _number_clause(column, value)
        elif kind == TEXT:
            sql, params = _text_clause(column, value)
        else:
            sql, params = _special_clause(field_name, value)
        described = f"{field_name}:{value}"
    else:
        if colon and value and not resolve(name):
            # Not a field we know. Search the whole thing as words rather than refusing.
            pass
        sql, params = _word_clause(body)
        described = f'"{body}"'
    if negate:
        sql = f"(NOT {sql})"
        described = f"not {described}"
    return sql, params, described


def compile_query(text: str) -> Compiled:
    """The search line to one WHERE clause. Terms are ANDed; `OR` joins its neighbours."""
    result = Compiled()
    groups: list[list[tuple[str, list]]] = [[]]
    pending_or = False
    for token in _tokens(text or ""):
        if token.upper() == "OR":
            pending_or = True
            continue
        try:
            sql, params, described = term_clause(token)
        except QueryError as error:
            result.errors.append(str(error))
            continue
        result.terms.append(described)
        if pending_or and groups[-1]:
            groups[-1].append((sql, params))
        else:
            groups.append([(sql, params)])
        pending_or = False
    clauses, params = [], []
    for group in groups:
        if not group:
            continue
        if len(group) == 1:
            clauses.append(group[0][0])
            params.extend(group[0][1])
        else:
            clauses.append("(" + " OR ".join(sql for sql, _p in group) + ")")
            for _sql, group_params in group:
                params.extend(group_params)
    if clauses:
        result.where = " AND ".join(clauses)
        result.params = params
    return result
