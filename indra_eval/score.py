"""SIGNOR precision/recall/F1 scoring for INDRA statements (see docs/SCORING.md).

Core match key: ``(subject_id, object_id, aspect, direction)``, matched within a
PMID. ``aspect=None`` on the SIGNOR side is a wildcard. ``Complex`` statements
match SIGNOR "form complex" rows on the unordered id pair.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

CAVEAT = (
    "SIGNOR is a curated signaling-subfield sample (mostly human protein "
    "signaling, hand-picked sentences). These numbers describe agreement with "
    "that slice of the literature only and do NOT generalize to the full "
    "literature or to non-signaling claims."
)

# INDRA statement type -> (aspect, direction). Modifications are handled by the
# de-/mod prefix rule below, so only the non-modification types are listed here.
SIMPLE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "Activation": ("activity", "increased"),
    "Inhibition": ("activity", "decreased"),
    "IncreaseAmount": ("abundance", "increased"),
    "DecreaseAmount": ("abundance", "decreased"),
    "GtpActivation": ("activity", "increased"),
}

# INDRA Modification subclasses (indra.statements.statements) — forward form.
MODIFICATION_TYPES = (
    "Phosphorylation",
    "Dephosphorylation",
    "Ubiquitination",
    "Deubiquitination",
    "Sumoylation",
    "Desumoylation",
    "Acetylation",
    "Deacetylation",
    "Hydroxylation",
    "Dehydroxylation",
    "Farnesylation",
    "Defarnesylation",
    "Geranylgeranylation",
    "Degeranylgeranylation",
    "Palmitoylation",
    "Depalmitoylation",
    "Myristoylation",
    "Demyristoylation",
    "Ribosylation",
    "Deribosylation",
    "Glycosylation",
    "Deglycosylation",
    "Methylation",
    "Demethylation",
)

COMPLEX_PREDICATE = "in_complex_with"

# rule 2 — SIGNOR MECHANISM -> (PTM family, direction) for mechanism-aware
# matching of INDRA Modification candidates. SIGNOR curates
# "PP1a dephosphorylates KAP1" as EFFECT=(activity, increased) +
# MECHANISM=dephosphorylation, while a correct extraction is
# Dephosphorylation(PP1, KAP1) -> (phosphorylation, decreased): same claim.
MECHANISM_PTM_MAP: dict[str, tuple[str, str]] = {
    "phosphorylation": ("phosphorylation", "increased"),
    "dephosphorylation": ("phosphorylation", "decreased"),
    "ubiquitination": ("ubiquitination", "increased"),
    "polyubiquitination": ("ubiquitination", "increased"),
    "monoubiquitination": ("ubiquitination", "increased"),
    "deubiquitination": ("ubiquitination", "decreased"),
    "acetylation": ("acetylation", "increased"),
    "deacetylation": ("acetylation", "decreased"),
    "methylation": ("methylation", "increased"),
    "trimethylation": ("methylation", "increased"),
    "demethylation": ("methylation", "decreased"),
    "sumoylation": ("sumoylation", "increased"),
    "desumoylation": ("sumoylation", "decreased"),
    "glycosylation": ("glycosylation", "increased"),
    "deglycosylation": ("glycosylation", "decreased"),
    "hydroxylation": ("hydroxylation", "increased"),
    "palmitoylation": ("palmitoylation", "increased"),
    "adp-ribosylation": ("ribosylation", "increased"),
}

# rule 1 — GT rows on SIGNOR-native ids (complexes, families, phenotypes,
# stimuli) can never match a UniProt-grounded candidate; they are out of the
# CURIE-matching game (FE addendum section 8) and excluded from the denominator.
SIGNOR_NATIVE_PREFIX = "SIGNOR"

# id-resolution modes, per candidate
UP_DIRECT = "up-direct"
HGNC_MAPPED = "hgnc-mapped"
UNGROUNDED = "ungrounded"
UNMAPPABLE_TYPE = "unmappable-type"


@dataclass
class CandidateTuple:
    """One INDRA statement reduced to the scoring match key."""

    stmt_type: str
    subject_id: str | None
    object_id: str | None
    aspect: str | None
    direction: str | None
    predicate: str | None = None  # 'in_complex_with' for Complex
    subject_name: str = ""
    object_name: str = ""
    id_mode: str = UNGROUNDED  # worst of the two agents' modes
    subject_mode: str = UNGROUNDED
    object_mode: str = UNGROUNDED
    mappable: bool = True  # False when the statement type has no SIGNOR mapping
    belief: float = 0.0

    @property
    def grounded(self) -> bool:
        return bool(self.subject_id) and bool(self.object_id)

    def key(self) -> tuple:
        return (
            self.subject_id,
            self.object_id,
            self.aspect,
            self.direction,
            self.predicate,
        )


@dataclass
class PaperScore:
    pmid: str
    tp: int = 0
    fp_strict: int = 0
    fp_grounded: int = 0
    fn: int = 0
    tp_core: int = 0
    tp_mechanism: int = 0
    n_gt: int = 0  # matchable rows = the headline recall denominator
    n_gt_unmatchable: int = 0  # rule 1: SIGNOR-native ids
    n_gt_skipped: int = 0
    n_candidates: int = 0
    n_mappable: int = 0
    n_grounded: int = 0
    n_unmappable_type: int = 0
    n_duplicate_keys: int = 0
    mech_tp: Counter = field(default_factory=Counter)
    mech_total: Counter = field(default_factory=Counter)
    type_tp: Counter = field(default_factory=Counter)
    type_total: Counter = field(default_factory=Counter)  # mappable candidates by type
    mode_counts: Counter = field(default_factory=Counter)
    matched_pairs: list = field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------
# statements -> candidate tuples
# --------------------------------------------------------------------------


def _resolve_agent(agent: Any) -> tuple[str | None, str]:
    """Return (uniprot_id, mode) for one agent, per the scoring spec's preference."""
    if agent is None:
        return None, UNGROUNDED
    refs = getattr(agent, "db_refs", None) or {}
    up = refs.get("UP")
    if up:
        return str(up).split(",")[0].strip(), UP_DIRECT
    hgnc = refs.get("HGNC")
    if hgnc:
        try:
            from indra.databases import hgnc_client

            mapped = hgnc_client.get_uniprot_id(str(hgnc))
        except Exception:  # noqa: BLE001 - offline/lookup failure is just ungrounded
            mapped = None
        if mapped:
            return str(mapped).split(",")[0].strip(), HGNC_MAPPED
    return None, UNGROUNDED


def _mod_mapping(stmt_type: str) -> tuple[str, str] | None:
    if stmt_type not in MODIFICATION_TYPES:
        return None
    if stmt_type.startswith("De"):
        return stmt_type[2:].lower(), "decreased"
    return stmt_type.lower(), "increased"


def candidate_tuples(stmts: Iterable[Any]) -> list[CandidateTuple]:
    """Map INDRA statements to candidate tuples."""
    out: list[CandidateTuple] = []
    for stmt in stmts:
        stmt_type = type(stmt).__name__
        agents = [a for a in (stmt.agent_list() or []) if a is not None]
        belief = float(getattr(stmt, "belief", 0.0) or 0.0)

        if stmt_type == "Complex":
            members = [m for m in (getattr(stmt, "members", None) or agents) if m]
            # Only pairwise complexes carry a usable core key; emit all pairs.
            resolved = [(_resolve_agent(m), getattr(m, "name", "")) for m in members]
            for i in range(len(resolved)):
                for j in range(i + 1, len(resolved)):
                    (aid, amode), aname = resolved[i]
                    (bid, bmode), bname = resolved[j]
                    out.append(
                        CandidateTuple(
                            stmt_type=stmt_type,
                            subject_id=aid,
                            object_id=bid,
                            aspect=None,
                            direction=None,
                            predicate=COMPLEX_PREDICATE,
                            subject_name=aname,
                            object_name=bname,
                            subject_mode=amode,
                            object_mode=bmode,
                            id_mode=_worst(amode, bmode),
                            belief=belief,
                        )
                    )
            continue

        mapping = SIMPLE_TYPE_MAP.get(stmt_type) or _mod_mapping(stmt_type)
        subj = agents[0] if agents else None
        obj = agents[1] if len(agents) > 1 else None
        (sid, smode) = _resolve_agent(subj)
        (oid, omode) = _resolve_agent(obj)
        if mapping is None:
            out.append(
                CandidateTuple(
                    stmt_type=stmt_type,
                    subject_id=sid,
                    object_id=oid,
                    aspect=None,
                    direction=None,
                    subject_name=getattr(subj, "name", ""),
                    object_name=getattr(obj, "name", ""),
                    subject_mode=smode,
                    object_mode=omode,
                    id_mode=UNMAPPABLE_TYPE,
                    mappable=False,
                    belief=belief,
                )
            )
            continue
        aspect, direction = mapping
        out.append(
            CandidateTuple(
                stmt_type=stmt_type,
                subject_id=sid,
                object_id=oid,
                aspect=aspect,
                direction=direction,
                subject_name=getattr(subj, "name", ""),
                object_name=getattr(obj, "name", ""),
                subject_mode=smode,
                object_mode=omode,
                id_mode=_worst(smode, omode),
                belief=belief,
            )
        )
    return out


def _worst(a: str, b: str) -> str:
    order = {UNGROUNDED: 0, HGNC_MAPPED: 1, UP_DIRECT: 2}
    return a if order.get(a, 0) <= order.get(b, 0) else b


def candidates_from_run(run_dir) -> list[CandidateTuple]:
    """Load statements.json from a run dir and return candidate tuples."""
    import json
    from pathlib import Path

    from indra.statements import stmts_from_json

    path = Path(run_dir) / "statements.json"
    stmts = stmts_from_json(json.loads(path.read_text(encoding="utf-8")))
    return candidate_tuples(stmts)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def as_row(record: Any) -> dict:
    """Accept either a dict or a signor_gt.SignorRecord and return a dict."""
    if isinstance(record, dict):
        return record
    import dataclasses

    if dataclasses.is_dataclass(record):
        return dataclasses.asdict(record)
    return {
        k: getattr(record, k, None)
        for k in (
            "pmid", "subject_id", "subject_name", "object_id", "object_name",
            "aspect", "direction", "mechanism", "sentence", "signor_id",
            "predicate", "effect",
        )
    }


def _gt_is_complex(row: dict) -> bool:
    if row.get("predicate") == COMPLEX_PREDICATE:
        return True
    effect = (row.get("effect") or "").lower()
    return effect.startswith("form complex")


def _gt_usable(row: dict) -> bool:
    """A GT row counts in the recall denominator unless its EFFECT is unknown."""
    if row.get("skip") or row.get("skipped"):
        return False
    effect = (row.get("effect") or "").lower()
    if effect.startswith("unknown"):
        return False
    if _gt_is_complex(row):
        return True
    # usable if it carries a direction (aspect may be a None wildcard)
    return bool(row.get("direction"))


def is_signor_native(row: dict) -> bool:
    """rule 1: does either endpoint use a SIGNOR-native id?"""
    for key in ("subject_id", "object_id"):
        value = str(row.get(key) or "")
        if value.upper().startswith(SIGNOR_NATIVE_PREFIX):
            return True
    return False


def _mechanism_matches(cand: CandidateTuple, row: dict) -> bool:
    """rule 2: same (subject, object) + a PTM-consistent SIGNOR MECHANISM."""
    if cand.stmt_type not in MODIFICATION_TYPES or not cand.grounded:
        return False
    if cand.predicate == COMPLEX_PREDICATE:
        return False
    if cand.subject_id != row.get("subject_id") or cand.object_id != row.get("object_id"):
        return False
    mapped = MECHANISM_PTM_MAP.get((row.get("mechanism") or "").strip().lower())
    if mapped is None:
        return False
    return (cand.aspect, cand.direction) == mapped


def _matches(cand: CandidateTuple, row: dict) -> bool:
    if not cand.grounded:
        return False
    s, o = row.get("subject_id"), row.get("object_id")
    if not s or not o:
        return False
    if _gt_is_complex(row):
        if cand.predicate != COMPLEX_PREDICATE:
            return False
        return {cand.subject_id, cand.object_id} == {s, o}
    if cand.predicate == COMPLEX_PREDICATE:
        return False
    if cand.subject_id != s or cand.object_id != o:
        return False
    if row.get("direction") and cand.direction != row.get("direction"):
        return False
    aspect = row.get("aspect")
    if aspect is not None and cand.aspect != aspect:
        return False
    return True


def score_paper(
    candidates: list[CandidateTuple], gt_rows: list[dict], pmid: str = ""
) -> PaperScore:
    """Greedy one-to-one match of candidates against a paper's SIGNOR rows."""
    score = PaperScore(pmid=pmid)
    score.n_candidates = len(candidates)
    gt_rows = [as_row(r) for r in gt_rows]

    # dedupe identical core keys (preassembly is per-statement, not per-key)
    seen: set[tuple] = set()
    deduped: list[CandidateTuple] = []
    for cand in candidates:
        score.mode_counts[cand.id_mode] += 1
        if not cand.mappable:
            score.n_unmappable_type += 1
            continue
        if cand.key() in seen:
            score.n_duplicate_keys += 1
            continue
        seen.add(cand.key())
        deduped.append(cand)

    score.n_mappable = len(deduped)
    score.n_grounded = sum(1 for c in deduped if c.grounded)
    for cand in deduped:
        score.type_total[cand.stmt_type] += 1

    usable = [r for r in gt_rows if _gt_usable(r)]
    score.n_gt_skipped = len(gt_rows) - len(usable)
    matchable = [r for r in usable if not is_signor_native(r)]
    score.n_gt_unmatchable = len(usable) - len(matchable)
    score.n_gt = len(matchable)

    used: set[int] = set()
    for row in matchable:
        mech = row.get("mechanism") or "(none)"
        score.mech_total[mech] += 1
        hit, how = None, ""
        for idx, cand in enumerate(deduped):
            if idx in used:
                continue
            if _matches(cand, row):
                hit, how = idx, "core"
                break
        if hit is None:  # rule 2: fall back to a mechanism-aware match
            for idx, cand in enumerate(deduped):
                if idx in used:
                    continue
                if _mechanism_matches(cand, row):
                    hit, how = idx, "mechanism"
                    break
        if hit is None:
            score.fn += 1
            continue
        used.add(hit)
        score.tp += 1
        if how == "mechanism":
            score.tp_mechanism += 1
        else:
            score.tp_core += 1
        score.mech_tp[mech] += 1
        score.type_tp[deduped[hit].stmt_type] += 1
        score.matched_pairs.append(
            {
                "signor_id": row.get("signor_id", ""),
                "mechanism": mech,
                "match": how,
                "stmt_type": deduped[hit].stmt_type,
                "subject_id": deduped[hit].subject_id,
                "object_id": deduped[hit].object_id,
            }
        )

    score.fp_strict = len(deduped) - len(used)
    score.fp_grounded = score.n_grounded - len(used)
    return score


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def aggregate(paper_scores: list[PaperScore]) -> dict:
    """Micro-averaged metrics plus precision variants and slices."""
    tp = sum(s.tp for s in paper_scores)
    tp_core = sum(s.tp_core for s in paper_scores)
    tp_mech = sum(s.tp_mechanism for s in paper_scores)
    fn = sum(s.fn for s in paper_scores)
    gt_matchable = sum(s.n_gt for s in paper_scores)
    gt_unmatchable = sum(s.n_gt_unmatchable for s in paper_scores)
    fp_strict = sum(s.fp_strict for s in paper_scores)
    fp_grounded = sum(s.fp_grounded for s in paper_scores)

    mech_tp: Counter = Counter()
    mech_total: Counter = Counter()
    type_tp: Counter = Counter()
    type_total: Counter = Counter()
    modes: Counter = Counter()
    for s in paper_scores:
        mech_tp.update(s.mech_tp)
        mech_total.update(s.mech_total)
        type_tp.update(s.type_tp)
        type_total.update(s.type_total)
        modes.update(s.mode_counts)

    per_paper = []
    for s in paper_scores:
        strict = _prf(s.tp, s.fp_strict, s.fn)
        per_paper.append(
            {
                "pmid": s.pmid,
                "n_gt": s.n_gt,
                "n_gt_matchable": s.n_gt,
                "n_gt_unmatchable": s.n_gt_unmatchable,
                "n_gt_skipped": s.n_gt_skipped,
                "n_candidates": s.n_candidates,
                "n_mappable": s.n_mappable,
                "n_grounded": s.n_grounded,
                "n_unmappable_type": s.n_unmappable_type,
                "tp": s.tp,
                "tp_core": s.tp_core,
                "tp_mechanism": s.tp_mechanism,
                "fp_strict": s.fp_strict,
                "fp_grounded": s.fp_grounded,
                "fn": s.fn,
                "precision_strict": strict["precision"],
                "precision_grounded": _prf(s.tp, s.fp_grounded, s.fn)["precision"],
                "recall": strict["recall"],
                "f1_strict": strict["f1"],
                "note": s.note,
            }
        )

    return {
        "n_papers": len(paper_scores),
        "overall": _prf(tp, fp_strict, fn),
        "precision_strict": _prf(tp, fp_strict, fn)["precision"],
        "precision_grounded_only": _prf(tp, fp_grounded, fn)["precision"],
        "f1_grounded_only": _prf(tp, fp_grounded, fn)["f1"],
        "recall": _prf(tp, fp_strict, fn)["recall"],
        "tp_core": tp_core,
        "tp_mechanism": tp_mech,
        "recall_all_rows": round(tp / (gt_matchable + gt_unmatchable), 4)
        if (gt_matchable + gt_unmatchable)
        else 0.0,
        "totals": {
            "tp": tp,
            "tp_core": tp_core,
            "tp_mechanism": tp_mech,
            "fp_strict": fp_strict,
            "fp_grounded": fp_grounded,
            "fn": fn,
            "gt_matchable": gt_matchable,
            "gt_unmatchable": gt_unmatchable,
            "gt_all_rows": gt_matchable + gt_unmatchable,
            "gt_usable": gt_matchable,
            "gt_skipped": sum(s.n_gt_skipped for s in paper_scores),
            "candidates": sum(s.n_candidates for s in paper_scores),
            "mappable_candidates": sum(s.n_mappable for s in paper_scores),
            "grounded_candidates": sum(s.n_grounded for s in paper_scores),
            "unmappable_type": sum(s.n_unmappable_type for s in paper_scores),
        },
        "id_resolution_modes": dict(modes),
        "by_mechanism": {
            mech: {
                "gt_rows": mech_total[mech],
                "matched": mech_tp.get(mech, 0),
                "recall": round(mech_tp.get(mech, 0) / mech_total[mech], 4)
                if mech_total[mech]
                else 0.0,
            }
            for mech in sorted(mech_total, key=lambda m: -mech_total[m])
        },
        "by_statement_type": {
            stype: {
                "candidates": type_total[stype],
                "matched": type_tp.get(stype, 0),
                "precision": round(type_tp.get(stype, 0) / type_total[stype], 4)
                if type_total[stype]
                else 0.0,
            }
            for stype in sorted(type_total, key=lambda t: -type_total[t])
        },
        "per_paper": per_paper,
        "caveat": CAVEAT,
    }


# --------------------------------------------------------------------------
# micro-test
# --------------------------------------------------------------------------


def _micro_test() -> None:
    cands = [
        CandidateTuple("Activation", "P1", "P2", "activity", "increased",
                       id_mode=UP_DIRECT, subject_mode=UP_DIRECT,
                       object_mode=UP_DIRECT),
        CandidateTuple("Inhibition", "P9", "P8", "activity", "decreased",
                       id_mode=UP_DIRECT, subject_mode=UP_DIRECT,
                       object_mode=UP_DIRECT),
    ]
    gt = [
        {"pmid": "1", "subject_id": "P1", "object_id": "P2", "aspect": "activity",
         "direction": "increased", "mechanism": "phosphorylation",
         "effect": "up-regulates activity"},
        {"pmid": "1", "subject_id": "P3", "object_id": "P4", "aspect": None,
         "direction": "decreased", "mechanism": "binding",
         "effect": "down-regulates"},
        {"pmid": "1", "subject_id": "P5", "object_id": "P6", "aspect": None,
         "direction": None, "mechanism": "", "effect": "unknown"},
    ]
    s = score_paper(cands, gt, pmid="1")
    assert (s.tp, s.fp_strict, s.fn) == (1, 1, 1), (s.tp, s.fp_strict, s.fn)
    assert s.n_gt == 2 and s.n_gt_skipped == 1, (s.n_gt, s.n_gt_skipped)
    agg = aggregate([s])
    assert agg["overall"]["precision"] == 0.5, agg["overall"]
    assert agg["overall"]["recall"] == 0.5, agg["overall"]
    assert agg["overall"]["f1"] == 0.5, agg["overall"]

    # wildcard aspect + unordered complex + ungrounded strictness
    cands2 = [
        CandidateTuple("Phosphorylation", "P3", "P4", "phosphorylation", "decreased",
                       id_mode=UP_DIRECT, subject_mode=UP_DIRECT,
                       object_mode=UP_DIRECT),
        CandidateTuple("Complex", "PB", "PA", None, None,
                       predicate=COMPLEX_PREDICATE, id_mode=UP_DIRECT,
                       subject_mode=UP_DIRECT, object_mode=UP_DIRECT),
        CandidateTuple("Activation", None, "P2", "activity", "increased"),
    ]
    gt2 = [
        {"subject_id": "P3", "object_id": "P4", "aspect": None,
         "direction": "decreased", "mechanism": "dephosphorylation",
         "effect": "down-regulates"},
        {"subject_id": "PA", "object_id": "PB", "aspect": None, "direction": None,
         "predicate": COMPLEX_PREDICATE, "mechanism": "binding",
         "effect": "form complex"},
    ]
    s2 = score_paper(cands2, gt2, pmid="2")
    assert (s2.tp, s2.fn) == (2, 0), (s2.tp, s2.fn)
    assert s2.fp_strict == 1 and s2.fp_grounded == 0, (s2.fp_strict, s2.fp_grounded)

    # rule 1: SIGNOR-native GT row is excluded from the denominator, counted.
    # rule 2: EFFECT=(activity, increased) + MECHANISM=dephosphorylation is
    # the same claim as a Dephosphorylation candidate -> (phosphorylation, decreased).
    cands3 = [
        CandidateTuple("Dephosphorylation", "P30153", "Q13263", "phosphorylation",
                       "decreased", id_mode=UP_DIRECT, subject_mode=UP_DIRECT,
                       object_mode=UP_DIRECT),
    ]
    gt3 = [
        {"subject_id": "P30153", "object_id": "Q13263", "aspect": "activity",
         "direction": "increased", "mechanism": "dephosphorylation",
         "effect": "up-regulates activity"},
        {"subject_id": "SIGNOR-C15", "object_id": "P04637", "aspect": "activity",
         "direction": "decreased", "mechanism": "binding",
         "effect": "down-regulates activity"},
    ]
    s3 = score_paper(cands3, gt3, pmid="3")
    assert s3.n_gt == 1 and s3.n_gt_unmatchable == 1, (s3.n_gt, s3.n_gt_unmatchable)
    assert (s3.tp, s3.tp_core, s3.tp_mechanism, s3.fn) == (1, 0, 1, 0), (
        s3.tp, s3.tp_core, s3.tp_mechanism, s3.fn)
    assert s3.fp_strict == 0, s3.fp_strict
    agg3 = aggregate([s3])
    assert agg3["overall"]["recall"] == 1.0, agg3["overall"]
    assert agg3["recall_all_rows"] == 0.5, agg3["recall_all_rows"]
    assert agg3["totals"]["gt_unmatchable"] == 1, agg3["totals"]
    print("score.py micro-test OK")


if __name__ == "__main__":
    _micro_test()
