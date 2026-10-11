"""The `indra-eval benchmark` harness: run a corpus, score it against SIGNOR."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from indra_eval import score

READERS = ("reach", "reach-modern", "trips")


def _reader_of_run(run_dir: Path) -> str | None:
    """Read the reader name back out of a run's summary.md, if present."""
    summary = run_dir / "summary.md"
    if not summary.exists():
        return None
    for line in summary.read_text(encoding="utf-8").splitlines():
        if line.startswith("- reader:"):
            return line.split(":", 1)[1].strip().strip("*").strip()
    return None


def _cached(run_dir: Path, reader: str) -> bool:
    return (run_dir / "statements.json").exists() and _reader_of_run(run_dir) == reader


def run(
    reader: str = "reach",
    corpus_path: str | Path = "benchmark/corpus.json",
    limit: int | None = None,
    out_root: str | Path = "benchmark",
    runs_root: str | Path = "runs",
    verbose: bool = True,
) -> dict:
    """Read + score every paper in the corpus manifest; write the three outputs."""
    if reader not in READERS:
        raise ValueError(f"unknown reader {reader!r}; expected one of {READERS}")

    from indra_eval import pipeline, signor_gt

    started = time.time()
    manifest = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    papers = manifest.get("papers", [])
    if limit is not None:
        papers = papers[:limit]

    gt_by_pmid = signor_gt.by_pmid(signor_gt.load(species=("human",)))

    paper_scores: list[score.PaperScore] = []
    rows: list[dict] = []
    failures: list[dict] = []
    skipped: list[dict] = []

    for i, paper in enumerate(papers, 1):
        pmid = str(paper.get("pmid", "")).strip()
        pmcid = str(paper.get("pmcid", "")).strip()
        target = pmcid or pmid
        gt_rows = gt_by_pmid.get(pmid, [])
        if not gt_rows:
            skipped.append({"pmid": pmid, "pmcid": pmcid,
                            "reason": "no usable human SIGNOR rows for this PMID"})
            if verbose:
                print(f"[benchmark] [{i}/{len(papers)}] {target} skipped: no GT rows",
                      flush=True)
            continue

        run_dir = Path(runs_root) / target
        cached = _cached(run_dir, reader)
        try:
            if cached:
                if verbose:
                    print(f"[benchmark] [{i}/{len(papers)}] {target} cached "
                          f"({reader})", flush=True)
            else:
                if verbose:
                    print(f"[benchmark] [{i}/{len(papers)}] {target} reading with "
                          f"{reader} ...", flush=True)
                run_dir = pipeline.run(target, reader=reader, out_root=str(runs_root))
            candidates = score.candidates_from_run(run_dir)
        except Exception as err:  # noqa: BLE001 - one bad paper must not kill the run
            failures.append({"pmid": pmid, "pmcid": pmcid,
                             "reason": f"{type(err).__name__}: {err}"})
            if verbose:
                print(f"[benchmark]   FAILED {target}: {type(err).__name__}: {err}",
                      flush=True)
            continue

        ps = score.score_paper(candidates, gt_rows, pmid=pmid)
        ps.note = "cached run" if cached else ""
        paper_scores.append(ps)
        rows.append({"pmid": pmid, "pmcid": pmcid,
                     "run_dir": str(run_dir), "cached": cached})
        if verbose:
            print(f"[benchmark]   tp={ps.tp} fp={ps.fp_strict} fn={ps.fn} "
                  f"(gt={ps.n_gt}, candidates={ps.n_candidates})", flush=True)

    results = score.aggregate(paper_scores)
    results.update({
        "reader": reader,
        "corpus": str(corpus_path),
        "limit": limit,
        "seconds": round(time.time() - started, 1),
        "papers_requested": len(papers),
        "papers_scored": len(paper_scores),
        "papers_failed": failures,
        "papers_skipped": skipped,
        "runs": rows,
    })

    out_dir = Path(out_root) / reader
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(
        json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
    )
    _write_per_paper(out_dir / "per_paper.tsv", results, rows)
    _write_report(out_dir / "report.md", results)
    if verbose:
        print(f"[benchmark] wrote {out_dir}/scores.json, per_paper.tsv, report.md")
    return results


PER_PAPER_COLUMNS = [
    "pmid", "pmcid", "n_gt_matchable", "n_gt_unmatchable", "n_candidates",
    "n_mappable", "n_grounded",
    "n_unmappable_type", "tp", "tp_core", "tp_mechanism",
    "fp_strict", "fp_grounded", "fn",
    "precision_strict", "precision_grounded", "recall", "f1_strict",
    "cached", "run_dir",
]


def _write_per_paper(path: Path, results: dict, rows: list[dict]) -> Path:
    by_pmid = {r["pmid"]: r for r in rows}
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(PER_PAPER_COLUMNS)
        for pp in results["per_paper"]:
            extra = by_pmid.get(pp["pmid"], {})
            writer.writerow([
                pp["pmid"], extra.get("pmcid", ""), pp["n_gt_matchable"],
                pp["n_gt_unmatchable"], pp["n_candidates"],
                pp["n_mappable"], pp["n_grounded"], pp["n_unmappable_type"],
                pp["tp"], pp["tp_core"], pp["tp_mechanism"],
                pp["fp_strict"], pp["fp_grounded"], pp["fn"],
                pp["precision_strict"], pp["precision_grounded"], pp["recall"],
                pp["f1_strict"], str(extra.get("cached", "")).lower(),
                extra.get("run_dir", ""),
            ])
    return path


def _pct(value: Any) -> str:
    try:
        return f"{100.0 * float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _write_report(path: Path, r: dict) -> Path:
    o = r["overall"]
    t = r["totals"]
    lines = [
        f"# indra-eval benchmark — reader `{r['reader']}` vs SIGNOR",
        "",
        f"- corpus: `{r['corpus']}` (limit={r['limit']})",
        f"- papers scored: **{r['papers_scored']}** of {r['papers_requested']} "
        f"requested ({len(r['papers_failed'])} failed, "
        f"{len(r['papers_skipped'])} skipped)",
        f"- wall-clock: {r['seconds']}s",
        "",
        "## Headline (micro-averaged, strict precision)",
        "",
        "| metric | value |",
        "|---|---|",
        f"| precision | {_pct(o['precision'])} |",
        f"| recall | {_pct(o['recall'])} |",
        f"| F1 | {_pct(o['f1'])} |",
        f"| TP / FP / FN | {o['tp']} / {o['fp']} / {o['fn']} |",
        f"| of TP: core-key / mechanism-aware | {r['tp_core']} / "
        f"{r['tp_mechanism']} |",
        "",
        f"Secondary: recall over **all** usable SIGNOR rows, including the "
        f"{t['gt_unmatchable']} SIGNOR-native rows no CURIE-matched extraction "
        f"could ever hit, is {_pct(r['recall_all_rows'])} "
        f"({o['tp']}/{t['gt_all_rows']}).",
        "",
        "### The two scoring adjustments (applied identically to every reader)",
        "",
        f"1. **SIGNOR-native entities excluded** — a GT row whose subject or object "
        f"id starts with `SIGNOR` (complexes, protein families, phenotypes, "
        f"stimuli) is dropped from the recall denominator, because no "
        f"UniProt-grounded candidate can ever match it. Here: "
        f"{t['gt_unmatchable']} of {t['gt_all_rows']} rows.",
        f"2. **Mechanism-aware Modification match** — SIGNOR curates "
        f"\"PP1a dephosphorylates KAP1\" as EFFECT=(activity, increased) + "
        f"MECHANISM=dephosphorylation, while a correct extraction is "
        f"`Dephosphorylation(PP1, KAP1)` -> (phosphorylation, decreased). A "
        f"Modification candidate therefore also matches on "
        f"`(subject_id, object_id)` when the SIGNOR MECHANISM maps to the same "
        f"PTM family with a consistent direction. Here: {r['tp_mechanism']} of "
        f"{o['tp']} TPs came from this rule.",
        "",
        "## Precision variants",
        "",
        "| variant | precision | F1 |",
        "|---|---|---|",
        f"| strict (ungrounded extractions count as FP) | "
        f"{_pct(r['precision_strict'])} | {_pct(o['f1'])} |",
        f"| grounded-only (both agents resolved to UniProt) | "
        f"{_pct(r['precision_grounded_only'])} | {_pct(r['f1_grounded_only'])} |",
        "",
        "## Denominators",
        "",
        "| quantity | count |",
        "|---|---|",
        f"| SIGNOR rows with usable EFFECT | {t['gt_all_rows']} |",
        f"| of those, matchable — `n_gt_matchable`, the headline recall "
        f"denominator | {t['gt_matchable']} |",
        f"| of those, SIGNOR-native and unmatchable — `n_gt_unmatchable` "
        f"(rule 1) | {t['gt_unmatchable']} |",
        f"| SIGNOR rows skipped (unusable EFFECT) | {t['gt_skipped']} |",
        f"| candidate tuples from statements | {t['candidates']} |",
        f"| of those, SIGNOR-mappable types | {t['mappable_candidates']} |",
        f"| of those, both ids resolved | {t['grounded_candidates']} |",
        f"| unmappable statement types (excluded from precision) | "
        f"{t['unmappable_type']} |",
        "",
        "### Entity id resolution modes (per candidate)",
        "",
        "| mode | count |",
        "|---|---|",
    ]
    for mode, count in sorted(r["id_resolution_modes"].items(), key=lambda x: -x[1]):
        lines.append(f"| {mode} | {count} |")

    lines += ["", "## Recall by SIGNOR mechanism", "",
              "| mechanism | gt rows | matched | recall |", "|---|---|---|---|"]
    if r["by_mechanism"]:
        for mech, s in r["by_mechanism"].items():
            lines.append(f"| {mech or '(none)'} | {s['gt_rows']} | {s['matched']} | "
                         f"{_pct(s['recall'])} |")
    else:
        lines.append("| (none) | 0 | 0 | n/a |")

    lines += ["", "## Precision by INDRA statement type", "",
              "| statement type | candidates | matched | precision |",
              "|---|---|---|---|"]
    if r["by_statement_type"]:
        for stype, s in r["by_statement_type"].items():
            lines.append(f"| {stype} | {s['candidates']} | {s['matched']} | "
                         f"{_pct(s['precision'])} |")
    else:
        lines.append("| (none) | 0 | 0 | n/a |")

    lines += ["", "## Per paper", "",
              "| pmid | gt matchable | gt unmatchable | candidates | tp | "
              "tp (mech) | fp | fn | P (strict) | R |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for pp in r["per_paper"]:
        lines.append(
            f"| {pp['pmid']} | {pp['n_gt_matchable']} | "
            f"{pp['n_gt_unmatchable']} | {pp['n_candidates']} | {pp['tp']} | "
            f"{pp['tp_mechanism']} | {pp['fp_strict']} | {pp['fn']} | "
            f"{_pct(pp['precision_strict'])} | {_pct(pp['recall'])} |"
        )

    if r["papers_failed"]:
        lines += ["", "## Failed papers", "", "| pmid | pmcid | reason |",
                  "|---|---|---|"]
        for f in r["papers_failed"]:
            lines.append(f"| {f['pmid']} | {f['pmcid']} | {f['reason']} |")
    if r["papers_skipped"]:
        lines += ["", "## Skipped papers", "", "| pmid | pmcid | reason |",
                  "|---|---|---|"]
        for s in r["papers_skipped"]:
            lines.append(f"| {s['pmid']} | {s['pmcid']} | {s['reason']} |")

    lines += [
        "",
        "## Honest-reporting caveat",
        "",
        f"> {score.CAVEAT}",
        "",
        "Matching uses the frozen core key `(subject_id, object_id, aspect, "
        "direction)` within a PMID; SIGNOR rows with a bare up/down-regulates "
        "EFFECT wildcard the aspect, and `form complex` rows match INDRA "
        "`Complex` statements on the unordered id pair.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
