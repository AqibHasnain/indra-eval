"""`indra-eval` command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

READERS = ("reach", "reach-modern", "trips")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="indra-eval",
        description="Evaluation harness for INDRA's text-reading pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("read", help="read a PMCID, PMID, or raw text")
    read.add_argument("target", help='PMCID (PMC123456), PMID (123456), or "raw text"')
    read.add_argument(
        "--reader",
        choices=READERS,
        default="reach",
        help="reader to use (default: reach)",
    )
    read.add_argument(
        "--out", default="runs", help="output root directory (default: runs)"
    )

    reach = sub.add_parser("reach", help="manage the local REACH docker service")
    reach.add_argument("action", choices=("start", "stop", "status"))
    reach.add_argument(
        "--no-wait",
        action="store_true",
        help="start: do not wait for the service to become ready",
    )
    reach.add_argument(
        "--modern",
        action="store_true",
        help="act on the modern REACH variant (arm64, port 8081) instead of v14",
    )

    signor = sub.add_parser("signor", help="SIGNOR database demo")
    signor.add_argument(
        "--out", default="runs/signor", help="output directory (default: runs/signor)"
    )

    corpus = sub.add_parser("corpus", help="build the SIGNOR benchmark corpus manifest")
    corpus.add_argument("action", choices=("build",))
    corpus.add_argument("--n", type=int, default=50, help="papers to sample (default: 50)")
    corpus.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")

    bench = sub.add_parser("benchmark", help="score a reader against SIGNOR ground truth")
    bench.add_argument(
        "--reader", choices=READERS, default="reach", help="reader to score (default: reach)"
    )
    bench.add_argument(
        "--corpus",
        default="benchmark/corpus.json",
        help="corpus manifest (default: benchmark/corpus.json)",
    )
    bench.add_argument("--limit", type=int, default=None, help="only the first N papers")
    bench.add_argument(
        "--out", default="benchmark", help="output root directory (default: benchmark)"
    )
    bench.add_argument(
        "--runs", default="runs", help="run directory root to read/write (default: runs)"
    )

    return parser


def _cmd_read(args: argparse.Namespace) -> int:
    from indra_eval import pipeline

    try:
        out_dir = pipeline.run(args.target, reader=args.reader, out_root=args.out)
    except Exception as err:  # noqa: BLE001 - one-line message, non-zero exit
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 1

    print(f"wrote {out_dir}/")
    for name in ("statements.json", "findings.tsv", "report.html", "summary.md"):
        print(f"  {name}")
    return 0


def _cmd_reach(args: argparse.Namespace) -> int:
    from indra_eval import reach_service

    variant = "modern" if getattr(args, "modern", False) else "v14"
    if args.action == "start":
        result = reach_service.start(wait=not args.no_wait, variant=variant)
    elif args.action == "stop":
        result = reach_service.stop(variant=variant)
    else:
        result = reach_service.status(variant=variant)

    print(result.get("message", result))
    if args.action == "stop":
        return 0
    return 0 if result.get("ready") else 1


def _cmd_signor(args: argparse.Namespace) -> int:
    try:
        from indra_eval import signor_demo
    except ImportError as err:
        print(
            f"error: the signor demo is not available in this build ({err})",
            file=sys.stderr,
        )
        return 1
    try:
        signor_demo.run(Path(args.out))
    except Exception as err:  # noqa: BLE001
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    from indra_eval import corpus

    try:
        manifest = corpus.build(n=args.n, seed=args.seed)
    except Exception as err:  # noqa: BLE001
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print(f"corpus: {len(manifest.get('papers', []))} papers")
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    from indra_eval import benchmark

    try:
        results = benchmark.run(
            reader=args.reader,
            corpus_path=args.corpus,
            limit=args.limit,
            out_root=args.out,
            runs_root=args.runs,
        )
    except Exception as err:  # noqa: BLE001
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 1

    overall = results["overall"]
    print(
        f"{results['reader']}: precision={overall['precision']:.3f} "
        f"(grounded-only {results['precision_grounded_only']:.3f}) "
        f"recall={overall['recall']:.3f} f1={overall['f1']:.3f} "
        f"[tp={overall['tp']} fp={overall['fp']} fn={overall['fn']}, "
        f"{results['papers_scored']} papers]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "read":
        return _cmd_read(args)
    if args.command == "reach":
        return _cmd_reach(args)
    if args.command == "corpus":
        return _cmd_corpus(args)
    if args.command == "benchmark":
        return _cmd_benchmark(args)
    return _cmd_signor(args)


if __name__ == "__main__":
    raise SystemExit(main())
