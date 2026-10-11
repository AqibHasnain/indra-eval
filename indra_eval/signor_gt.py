"""SIGNOR ground-truth loader and normalizer (see docs/SCORING.md).

Reads the headerless 28-column SIGNOR TSV dumps in `signor_landing_data/` and
normalizes each row into a scoring record keyed by `(subject_id, object_id,
aspect, direction)` within a PMID.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "signor_landing_data"

# 28 positional columns, in file order.
COLUMNS = [
    "ENTITY_A", "TYPE_A", "ID_A", "DATABASE_A",
    "ENTITY_B", "TYPE_B", "ID_B", "DATABASE_B",
    "EFFECT", "MECHANISM", "RESIDUE", "SEQUENCE",
    "TAX_ID", "CELL_DATA", "TISSUE_DATA",
    "MODULATOR_COMPLEX", "TARGET_COMPLEX",
    "MODIFICATION_A", "MODASEQ", "MODIFICATION_B", "MODBSEQ",
    "PMID", "DIRECT", "NOTES", "ANNOTATOR", "SENTENCE",
    "SIGNOR_ID", "SCORE",
]

COMPLEX_PREDICATE = "in_complex_with"

ASPECT_ACTIVITY = "activity"
ASPECT_ABUNDANCE = "abundance"


@dataclass
class SignorRecord:
    """Normalized SIGNOR ground-truth record (see docs/SCORING.md)."""

    pmid: str
    subject_id: str
    subject_name: str
    object_id: str
    object_name: str
    aspect: str | None
    direction: str | None
    mechanism: str
    sentence: str
    signor_id: str
    predicate: str | None = None  # only set for 'form complex' rows
    species: str = "human"
    effect: str = ""

    @property
    def is_complex(self) -> bool:
        return self.predicate == COMPLEX_PREDICATE

    def key(self) -> tuple[str, str, str | None, str | None]:
        return (self.subject_id, self.object_id, self.aspect, self.direction)


@dataclass
class LoadStats:
    rows: int = 0
    excluded_unknown_effect: int = 0
    excluded_unmapped_effect: int = 0
    excluded_no_pmid: int = 0
    excluded_no_id: int = 0
    kept: int = 0
    buckets: Counter = field(default_factory=Counter)
    unmapped_effects: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not isinstance(v, Counter)}
        d["buckets"] = {str(k): v for k, v in sorted(self.buckets.items(), key=lambda x: -x[1])}
        d["unmapped_effects"] = dict(self.unmapped_effects)
        return d


def map_effect(effect: str) -> tuple[str | None, str | None, str | None, bool]:
    """Map a SIGNOR EFFECT string to (aspect, direction, predicate, usable).

    - "up-regulates activity"        -> (activity,  increased, None, True)
    - "down-regulates activity"      -> (activity,  decreased, None, True)
    - "up-regulates quantity*"       -> (abundance, increased, None, True)
    - "down-regulates quantity*"     -> (abundance, decreased, None, True)
    - bare "up-regulates"            -> (None,      increased, None, True)   # aspect wildcard
    - bare "down-regulates"          -> (None,      decreased, None, True)
    - "form complex"                 -> (None, None, in_complex_with, True)
    - "unknown" / anything else      -> (None, None, None, False)
    """
    e = (effect or "").strip().lower()
    if not e or e == "unknown":
        return (None, None, None, False)
    if e == "form complex":
        return (None, None, COMPLEX_PREDICATE, True)

    if e.startswith("up-regulates"):
        direction = "increased"
        rest = e[len("up-regulates"):].strip()
    elif e.startswith("down-regulates"):
        direction = "decreased"
        rest = e[len("down-regulates"):].strip()
    else:
        return (None, None, None, False)

    if rest == "":
        aspect = None
    elif rest.startswith("activity"):
        aspect = ASPECT_ACTIVITY
    elif rest.startswith("quantity"):
        aspect = ASPECT_ABUNDANCE
    else:
        return (None, None, None, False)
    return (aspect, direction, None, True)


def _path_for(species: str) -> Path:
    return DATA_DIR / f"signor_{species}.tsv"


def iter_rows(species: str = "human"):
    """Yield raw SIGNOR rows as dicts keyed by COLUMNS."""
    path = _path_for(species)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        for row in reader:
            if not row:
                continue
            if len(row) < len(COLUMNS):
                row = row + [""] * (len(COLUMNS) - len(row))
            yield dict(zip(COLUMNS, row))


def load(
    species: tuple[str, ...] | str = ("human",),
    stats: LoadStats | None = None,
) -> list[SignorRecord]:
    """Load and normalize SIGNOR rows. Unusable-EFFECT rows are dropped.

    Pass a `LoadStats` instance to collect exclusion/bucket counts.
    """
    if isinstance(species, str):
        species = (species,)
    if stats is None:
        stats = LoadStats()

    out: list[SignorRecord] = []
    for sp in species:
        for row in iter_rows(sp):
            stats.rows += 1
            effect = row["EFFECT"].strip()
            aspect, direction, predicate, usable = map_effect(effect)
            if not usable:
                if (effect or "").strip().lower() in ("", "unknown"):
                    stats.excluded_unknown_effect += 1
                else:
                    stats.excluded_unmapped_effect += 1
                    stats.unmapped_effects[effect] += 1
                continue
            pmid = row["PMID"].strip()
            if not pmid.isdigit():
                stats.excluded_no_pmid += 1
                continue
            subject_id = row["ID_A"].strip()
            object_id = row["ID_B"].strip()
            if not subject_id or not object_id:
                stats.excluded_no_id += 1
                continue
            out.append(
                SignorRecord(
                    pmid=pmid,
                    subject_id=subject_id,
                    subject_name=row["ENTITY_A"].strip(),
                    object_id=object_id,
                    object_name=row["ENTITY_B"].strip(),
                    aspect=aspect,
                    direction=direction,
                    mechanism=row["MECHANISM"].strip(),
                    sentence=row["SENTENCE"].strip(),
                    signor_id=row["SIGNOR_ID"].strip(),
                    predicate=predicate,
                    species=sp,
                    effect=effect,
                )
            )
            stats.kept += 1
            stats.buckets[predicate or (aspect, direction)] += 1
    return out


def by_pmid(records: list[SignorRecord]) -> dict[str, list[SignorRecord]]:
    """Group normalized records by PMID."""
    grouped: dict[str, list[SignorRecord]] = defaultdict(list)
    for rec in records:
        grouped[rec.pmid].append(rec)
    return dict(grouped)


def distribution(species: tuple[str, ...] = ("human",)) -> dict:
    """Load and return the exclusion/bucket distribution (for reporting)."""
    stats = LoadStats()
    records = load(species, stats=stats)
    d = stats.as_dict()
    d["distinct_pmids"] = len(by_pmid(records))
    return d


if __name__ == "__main__":
    import json

    print(json.dumps(distribution(), indent=2))
