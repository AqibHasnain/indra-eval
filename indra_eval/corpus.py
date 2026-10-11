"""Deterministic benchmark corpus builder (see docs/SCORING.md).

Candidate pool = PMIDs cited by human SIGNOR rows. Each is resolved to a PMCID
via NCBI idconv (cached to `benchmark/pmid_pmcid.json`, so the sweep runs once),
then a deterministic sample is drawn and each pick is verified to have real PMC
OA full text via efetch. Rejects are replaced by the next deterministic
candidate until `n` papers are verified.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from . import signor_gt

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = REPO_ROOT / "benchmark"
CACHE_PATH = BENCHMARK_DIR / "pmid_pmcid.json"
CORPUS_PATH = BENCHMARK_DIR / "corpus.json"

IDCONV_URL = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TOOL = "indra-eval"
EMAIL = os.environ.get("NCBI_EMAIL", "")

BATCH_SIZE = 200
MIN_INTERVAL = 0.34  # ~3 requests/second, politeness cap

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _get(url: str, params: dict) -> tuple[int, str]:
    _throttle()
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": f"{TOOL} ({EMAIL})"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        return exc.code, exc.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# PMID -> PMCID resolution
# --------------------------------------------------------------------------- #

def candidate_pmids() -> Counter:
    """Numeric PMIDs from human SIGNOR rows -> usable-row counts."""
    records = signor_gt.load(("human",))
    counts: Counter = Counter()
    for rec in records:
        counts[rec.pmid] += 1
    return counts


def load_cache() -> dict[str, str | None]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict[str, str | None]) -> None:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=0, sort_keys=True))


def resolve_pmcids(pmids: list[str], verbose: bool = True) -> dict[str, str | None]:
    """Resolve PMIDs to PMCIDs via idconv, using/updating the on-disk cache.

    Cache values are the PMCID string, or None when the paper is not in PMC.
    """
    cache = load_cache()
    todo = sorted({p for p in pmids if p not in cache}, key=int)
    if verbose and todo:
        print(f"[corpus] resolving {len(todo)} uncached PMIDs "
              f"({(len(todo) + BATCH_SIZE - 1) // BATCH_SIZE} batches)")

    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        status, body = _get(IDCONV_URL, {
            "ids": ",".join(batch), "format": "json", "tool": TOOL, "email": EMAIL,
        })
        if status != 200:
            print(f"[corpus] idconv batch {i // BATCH_SIZE} HTTP {status}; retrying once")
            time.sleep(3)
            status, body = _get(IDCONV_URL, {
                "ids": ",".join(batch), "format": "json", "tool": TOOL, "email": EMAIL,
            })
            if status != 200:
                print(f"[corpus] idconv batch {i // BATCH_SIZE} failed again; skipping")
                continue
        try:
            records = json.loads(body).get("records", [])
        except json.JSONDecodeError:
            print(f"[corpus] idconv batch {i // BATCH_SIZE} returned non-JSON; skipping")
            continue
        seen = set()
        for rec in records:
            pmid = str(rec.get("pmid") or "").strip()
            if not pmid:
                continue
            seen.add(pmid)
            pmcid = (rec.get("pmcid") or "").strip()
            cache[pmid] = pmcid or None
        for pmid in batch:  # PMIDs idconv did not return at all: not in PMC
            if pmid not in seen:
                cache[pmid] = None
        save_cache(cache)
        if verbose:
            done = min(i + BATCH_SIZE, len(todo))
            print(f"[corpus]   {done}/{len(todo)} resolved", flush=True)

    return {p: cache.get(p) for p in pmids}


# --------------------------------------------------------------------------- #
# OA full-text verification
# --------------------------------------------------------------------------- #

def has_oa_fulltext(pmcid: str) -> bool:
    """True iff efetch returns a PMC article XML that actually has a <body>."""
    status, body = _get(EFETCH_URL, {
        "db": "pmc", "id": pmcid, "rettype": "xml", "tool": TOOL, "email": EMAIL,
    })
    if status != 200:
        return False
    return "<article" in body and "<body" in body


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build(n: int = 50, seed: int = 42, verbose: bool = True) -> dict:
    """Build the deterministic n-paper OA benchmark corpus manifest."""
    row_counts = candidate_pmids()
    all_pmids = sorted(row_counts, key=int)
    if verbose:
        print(f"[corpus] {len(all_pmids)} distinct numeric PMIDs in human SIGNOR")

    mapping = resolve_pmcids(all_pmids, verbose=verbose)
    with_pmcid = [p for p in all_pmids if mapping.get(p)]
    overlap = {
        "total_pmids": len(all_pmids),
        "with_pmcid": len(with_pmcid),
        "rate": round(len(with_pmcid) / len(all_pmids), 4) if all_pmids else 0.0,
    }
    if verbose:
        print(f"[corpus] PMC overlap: {overlap['with_pmcid']}/{overlap['total_pmids']} "
              f"= {overlap['rate']}")

    # Deterministic ordering: shuffle the sorted candidate pool once, then walk
    # it in order, verifying OA full text and skipping rejects.
    pool = sorted(with_pmcid, key=int)
    order = random.Random(seed).sample(pool, len(pool))

    papers: list[dict] = []
    checked = 0
    rejected: list[str] = []
    for pmid in order:
        if len(papers) >= n:
            break
        pmcid = mapping[pmid]
        checked += 1
        if has_oa_fulltext(pmcid):
            papers.append({
                "pmid": pmid,
                "pmcid": pmcid,
                "signor_row_count": row_counts[pmid],
            })
            if verbose:
                print(f"[corpus] [{len(papers)}/{n}] {pmid} {pmcid} "
                      f"rows={row_counts[pmid]}", flush=True)
        else:
            rejected.append(pmid)

    manifest = {
        "seed": seed,
        "n": n,
        "oa_overlap": overlap,
        "verification": {
            "checked": checked,
            "accepted": len(papers),
            "rejected_no_fulltext": len(rejected),
        },
        "papers": papers,
    }
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(json.dumps(manifest, indent=2))
    if verbose:
        print(f"[corpus] wrote {CORPUS_PATH} ({len(papers)} papers)")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="indra-eval corpus build")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    m = build(n=args.n, seed=args.seed, verbose=not args.quiet)
    print(json.dumps({"papers": len(m["papers"]), "oa_overlap": m["oa_overlap"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
