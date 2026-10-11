"""The four outputs written into a run directory."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

FINDINGS_COLUMNS = [
    "type",
    "subject_text",
    "subject_grounding",
    "object_text",
    "object_grounding",
    "detail",
    "belief",
    "evidence_sentence",
    "pmid",
    "source_api",
]

# preference order for turning db_refs into a single NAMESPACE:ID
GROUNDING_PRIORITY = [
    "HGNC",
    "UP",
    "UPPRO",
    "FPLX",
    "CHEBI",
    "PUBCHEM",
    "MESH",
    "GO",
    "MIRBASE",
    "EFO",
    "DOID",
    "HP",
    "INDRA_ACTIVITIES",
]


def _grounding(agent: Any) -> str:
    if agent is None:
        return ""
    refs = getattr(agent, "db_refs", None) or {}
    for ns in GROUNDING_PRIORITY:
        if refs.get(ns):
            return f"{ns}:{refs[ns]}"
    for ns, value in refs.items():
        if ns != "TEXT" and value:
            return f"{ns}:{value}"
    return ""


def _agent_text(agent: Any) -> str:
    if agent is None:
        return ""
    refs = getattr(agent, "db_refs", None) or {}
    return refs.get("TEXT") or getattr(agent, "name", "") or ""


def _detail(stmt: Any) -> str:
    bits: list[str] = []
    residue = getattr(stmt, "residue", None)
    position = getattr(stmt, "position", None)
    if residue:
        bits.append(f"residue={residue}")
    if position:
        bits.append(f"position={position}")
    for attr in ("obj_activity", "subj_activity", "activity"):
        value = getattr(stmt, attr, None)
        if isinstance(value, str) and value:
            bits.append(f"{attr}={value}")
    members = getattr(stmt, "members", None)
    if members:
        bits.append(
            "members=" + "|".join(getattr(m, "name", "?") for m in members if m)
        )
    return ";".join(bits)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def write_statements_json(stmts: list[Any], out_dir: Path) -> Path:
    from indra.statements import stmts_to_json

    path = out_dir / "statements.json"
    path.write_text(
        json.dumps(stmts_to_json(stmts), indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def write_findings_tsv(stmts: list[Any], out_dir: Path) -> Path:
    path = out_dir / "findings.tsv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(FINDINGS_COLUMNS)
        for stmt in stmts:
            agents = list(stmt.agent_list())
            subj = agents[0] if agents else None
            obj = agents[1] if len(agents) > 1 else None
            evidence = stmt.evidence[0] if getattr(stmt, "evidence", None) else None
            writer.writerow(
                [
                    type(stmt).__name__,
                    _agent_text(subj),
                    _grounding(subj),
                    _agent_text(obj),
                    _grounding(obj),
                    _detail(stmt),
                    f"{getattr(stmt, 'belief', 0.0):.4f}",
                    _clean(getattr(evidence, "text", "")),
                    getattr(evidence, "pmid", "") or "",
                    getattr(evidence, "source_api", "") or "",
                ]
            )
    return path


def write_report_html(stmts: list[Any], out_dir: Path) -> Path:
    path = out_dir / "report.html"
    try:
        from indra.assemblers.html import HtmlAssembler

        HtmlAssembler(stmts).save_model(str(path))
    except Exception as err:  # noqa: BLE001 - stub out on any assembler failure
        path.write_text(
            "<html><body><h1>report unavailable</h1><p>HtmlAssembler failed: "
            f"{html.escape(f'{type(err).__name__}: {err}')}</p>"
            f"<p>{len(stmts)} statement(s) were assembled; see statements.json "
            "and findings.tsv.</p></body></html>",
            encoding="utf-8",
        )
    return path


def write_summary_md(
    stmts: list[Any],
    out_dir: Path,
    *,
    run_id: str,
    reader: str,
    target_kind: str,
    seconds: float,
    raw_count: int,
    notes: list[str] | None = None,
) -> Path:
    type_counts = Counter(type(s).__name__ for s in stmts)

    grounded = 0
    entities: Counter[str] = Counter()
    for stmt in stmts:
        agents = [a for a in stmt.agent_list() if a is not None]
        if agents and all(_grounding(a) for a in agents):
            grounded += 1
        for agent in agents:
            entities[getattr(agent, "name", "?")] += 1
    coverage = (100.0 * grounded / len(stmts)) if stmts else 0.0

    lines = [
        f"# indra-eval run `{run_id}`",
        "",
        f"- reader: **{reader}**",
        f"- input kind: {target_kind}",
        f"- raw statements from reader: {raw_count}",
        f"- assembled (deduplicated) statements: {len(stmts)}",
        f"- grounding coverage (all agents grounded): {coverage:.1f}%",
        f"- wall-clock: {seconds:.1f}s",
        "",
    ]
    for note in notes or []:
        lines.append(f"> note: {note}")
    if notes:
        lines.append("")

    lines += ["## Statements by type", "", "| type | count |", "|---|---|"]
    if type_counts:
        for name, count in type_counts.most_common():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("| (none) | 0 |")

    lines += ["", "## Top 10 entities", "", "| entity | mentions |", "|---|---|"]
    if entities:
        for name, count in entities.most_common(10):
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("| (none) | 0 |")
    lines.append("")

    path = out_dir / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_all(
    stmts: list[Any],
    out_dir: Path,
    *,
    run_id: str,
    reader: str,
    target_kind: str,
    seconds: float,
    raw_count: int,
    notes: list[str] | None = None,
) -> list[Path]:
    return [
        write_statements_json(stmts, out_dir),
        write_findings_tsv(stmts, out_dir),
        write_report_html(stmts, out_dir),
        write_summary_md(
            stmts,
            out_dir,
            run_id=run_id,
            reader=reader,
            target_kind=target_kind,
            seconds=seconds,
            raw_count=raw_count,
            notes=notes,
        ),
    ]
