"""SIGNOR database demo.

SIGNOR is a curated signaling-causal-interaction database. INDRA has a client
for it (``indra.sources.signor``) that downloads two TSVs from
signor.uniroma2.it and converts them into INDRA Statements.

As of 2026-08-20 signor.uniroma2.it is unreachable, so this module is written to
fail loudly and usefully: it tells you exactly what broke and how to rerun with
files you downloaded by hand.
"""

from __future__ import annotations

import collections
import json
import socket
import sys
from pathlib import Path

DATA_URL = "https://signor.uniroma2.it/download_entity.php"
COMPLEXES_URL = "https://signor.uniroma2.it/download_complexes.php"
RETRY_HINT = f"""
SIGNOR could not be reached. To run this demo offline:

  1. Download the two files by hand (browser or curl) when the site is back:
       all interactions TSV : {DATA_URL}
       complexes TSV        : {COMPLEXES_URL}
     (The site's Download page is https://signor.uniroma2.it/downloads.php ;
      the "all data" / "complexes" links are the ones you want.)
  2. Rerun pointing at the local copies:
       indra-eval signor --data-file signor_all_data.tsv --complexes-file signor_complexes.tsv
     or:
       uv run python -m indra_eval.signor_demo signor_all_data.tsv signor_complexes.tsv
"""


def _summarize(stmts, out_dir: Path) -> None:
    from indra.statements import stmts_to_json

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "statements.json").write_text(json.dumps(stmts_to_json(stmts), indent=1))

    counts = collections.Counter(type(s).__name__ for s in stmts)
    lines = ["type\tcount"] + [f"{t}\t{n}" for t, n in counts.most_common()]
    (out_dir / "type_counts.tsv").write_text("\n".join(lines) + "\n")

    top = ", ".join(f"{t}={n}" for t, n in counts.most_common(5))
    print(f"SIGNOR: {len(stmts)} statements, {len(counts)} distinct statement types")
    print(f"SIGNOR: top types: {top}")
    print(f"SIGNOR: statements.json  -> {out_dir / 'statements.json'}")
    print(f"SIGNOR: type_counts.tsv  -> {out_dir / 'type_counts.tsv'}")
    print("SIGNOR: done")


def run(
    out_dir: str | Path = "runs/signor",
    data_file: str | Path | None = None,
    complexes_file: str | Path | None = None,
) -> Path | None:
    """Process SIGNOR into INDRA Statements. Returns out_dir, or None on failure."""
    out_dir = Path(out_dir)
    from indra.sources import signor

    try:
        if data_file:
            print(f"SIGNOR: reading local file {data_file}")
            sp = signor.process_from_file(str(data_file),
                                          str(complexes_file) if complexes_file else None)
        else:
            print(f"SIGNOR: downloading from {DATA_URL} (60s timeout)")
            socket.setdefaulttimeout(60)
            sp = signor.process_from_web(signor_data_file=None, signor_complexes_file=None)
    except Exception as exc:  # network, HTTP, parse — all end the same way
        print(f"SIGNOR: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
        print(RETRY_HINT, file=sys.stderr)
        return None
    finally:
        socket.setdefaulttimeout(None)

    if sp is None or not sp.statements:
        print("SIGNOR: FAILED — processor returned no statements", file=sys.stderr)
        print(RETRY_HINT, file=sys.stderr)
        return None

    _summarize(sp.statements, out_dir)
    return out_dir


def main() -> None:
    args = sys.argv[1:]
    data_file = args[0] if len(args) > 0 else None
    complexes_file = args[1] if len(args) > 1 else None
    result = run(data_file=data_file, complexes_file=complexes_file)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
