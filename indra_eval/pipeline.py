"""The `indra-eval read` pipeline: fetch -> read -> assemble -> emit."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from indra_eval import fetch, outputs

REACH_TEXT_URL = "http://localhost:8080/api/text"
REACH_NXML_URL = "http://localhost:8080/api/uploadFile"
# the modern (arm64, clulab/reach master) REACH service, same routes on 8081
REACH_MODERN_TEXT_URL = "http://localhost:8081/api/text"
REACH_MODERN_NXML_URL = "http://localhost:8081/api/uploadFile"


def run(target: str, reader: str = "reach", out_root: str | Path = "runs") -> Path:
    """Run the full pipeline and return the run directory."""
    started = time.time()
    notes: list[str] = []

    run_id, kind, content = fetch.resolve(target)
    out_dir = Path(out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()  # REACH reading chdirs; keep paths absolute

    if run_id.isdigit():
        pmid = run_id
    elif run_id.startswith("PMC"):
        pmid = (
            fetch.pmid_from_nxml(content) if kind == "nxml" else ""
        ) or fetch.pmid_for_pmcid(run_id) or None
        if pmid is None:
            notes.append(
                f"could not resolve a PMID for {run_id}; the pmid column will be empty"
            )
    else:
        pmid = None

    if kind == "nxml" and reader == "trips":
        content = fetch.nxml_to_text(content)
        kind = "text"
        notes.append(
            "TRIPS is text-only: the PMC full-text XML was converted to plain text "
            "with pmc_client.extract_text() before reading."
        )

    raw = _read(content, kind, reader, out_dir, pmid, notes)
    if pmid:  # TRIPS takes no citation argument, so stamp anything left blank
        for stmt in raw:
            for ev in getattr(stmt, "evidence", None) or []:
                if not ev.pmid:
                    ev.pmid = pmid
    if not raw:
        raise RuntimeError(
            f"the {reader} reader returned no statements — "
            + (
                "check that the local REACH service is up ('indra-eval reach status'); "
                "under emulation it can take 5-15 minutes to boot"
                if reader.startswith("reach")
                else "the TRIPS web service may be unavailable or the input too long"
            )
        )

    stmts = _assemble(raw, notes)
    seconds = time.time() - started

    outputs.write_all(
        stmts,
        out_dir,
        run_id=run_id,
        reader=reader,
        target_kind=kind,
        seconds=seconds,
        raw_count=len(raw),
        notes=notes,
    )
    return out_dir


def _read(
    content: str,
    kind: str,
    reader: str,
    out_dir: Path,
    pmid: str | None,
    notes: list[str],
) -> list[Any]:
    if reader in ("reach", "reach-modern"):
        return _read_reach(content, kind, out_dir, pmid, modern=reader == "reach-modern")
    if reader == "trips":
        return _read_trips(content, out_dir)
    raise ValueError(
        f"unknown reader: {reader!r} "
        "(expected 'reach', 'reach-modern' or 'trips')"
    )


def _read_reach(
    content: str, kind: str, out_dir: Path, pmid: str | None, modern: bool = False
) -> list[Any]:
    from indra.sources import reach

    text_url = REACH_MODERN_TEXT_URL if modern else REACH_TEXT_URL
    nxml_url = REACH_MODERN_NXML_URL if modern else REACH_NXML_URL

    cwd = os.getcwd()
    os.chdir(out_dir)  # REACH helpers drop temp files into the CWD
    try:
        if kind == "nxml":
            nxml_path = out_dir / "input.nxml"
            nxml_path.write_text(content, encoding="utf-8")
            rp = reach.process_nxml_file(
                str(nxml_path),
                citation=pmid,
                url=nxml_url,
                output_fname=str(out_dir / "reach_output.json"),
            )
        else:
            (out_dir / "input.txt").write_text(content, encoding="utf-8")
            rp = reach.process_text(content, citation=pmid, url=text_url)
    finally:
        os.chdir(cwd)
    return list(rp.statements) if rp is not None else []


def _read_trips(content: str, out_dir: Path) -> list[Any]:
    from indra.sources import trips

    (out_dir / "input.txt").write_text(content, encoding="utf-8")
    rp = trips.process_text(content, save_xml_name=str(out_dir / "trips_output.xml"))
    return list(rp.statements) if rp is not None else []


def _assemble(stmts: list[Any], notes: list[str]) -> list[Any]:
    from indra.tools import assemble_corpus as ac

    try:
        stmts = ac.map_grounding(stmts)
    except Exception as err:  # noqa: BLE001
        notes.append(f"map_grounding skipped: {type(err).__name__}: {err}")
    try:
        stmts = ac.map_sequence(stmts)
    except Exception as err:  # noqa: BLE001
        notes.append(f"map_sequence skipped: {type(err).__name__}: {err}")
    return ac.run_preassembly(stmts, return_toplevel=True)
