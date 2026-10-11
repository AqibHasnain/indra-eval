# indra-eval

A command-line harness for running [INDRA](https://github.com/gyorilab/indra)'s text-reading pipeline on a paper and inspecting the result, plus a benchmark that scores the extracted statements against curated SIGNOR relations.

I wrote it to see what INDRA extracts from biomedical text (statements, groundings, evidence sentences, belief scores) and how accurate it is, without assembling the reader infrastructure by hand. It reads a PMCID, a PMID, or raw text with REACH or TRIPS, assembles the statements with INDRA's grounding, sequence-mapping and preassembly tools, and writes four output files per run.

Benchmark result on 50 SIGNOR-referenced PMC open-access papers (REACH 1.4): recall 37.8% over the 82 ground-truth relations that an extraction could match, and strict precision 2.9%. Precision is low largely because SIGNOR curates a small subset of what each paper states, so correct extractions that SIGNOR did not curate count as false positives. Details are in [`benchmark/reach/report.md`](benchmark/reach/report.md) and [`docs/SCORING.md`](docs/SCORING.md).

## Documentation

- [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md): walkthrough of each pipeline stage using a real run (PMC3898398), how to read an INDRA Statement, and how the benchmark scores a reader.
- [`docs/SCORING.md`](docs/SCORING.md): ground-truth normalization, match keys, and the two scoring adjustments.
- [`docs/GLOSSARY.md`](docs/GLOSSARY.md): INDRA, NLP and knowledge-graph terms used in the project.
- [`benchmark/COMPARISON.md`](benchmark/COMPARISON.md): REACH 1.4 against a build of current REACH master.

## Setup

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and Docker for the REACH reader.

```bash
uv sync
export NCBI_EMAIL=you@example.org   # sent to NCBI E-utilities, used by the fetch and benchmark commands
```

## Usage

```bash
indra-eval reach start                        # pull and start the local REACH container
indra-eval reach status                       # wait until it reports ready
indra-eval read PMC7096066                    # full text from PMC OA, read with local REACH
indra-eval read 32133153 --reader trips       # PubMed abstract, read with the TRIPS web service
indra-eval read "MEK phosphorylates ERK." --reader trips
```

Output goes to `runs/<id>/`, where `<id>` is the PMCID, the PMID, or `text-<hash8>` for raw text.

Run times:

- `reach start` pulls an amd64 image of about 1 GB. On Apple Silicon it runs under emulation, and the first request includes JVM start-up and model loading (5 to 15 minutes).
- A full paper through local REACH can take tens of minutes. Start with an abstract or a few sentences.
- TRIPS is a remote service that takes 1 to 3 minutes for an abstract. It is not suited to full papers.

## Readers

| Reader | Description |
|---|---|
| `reach` (default) | REACH 1.4 in Docker (`pathwaycommons/reach-docker`), managed by `indra-eval reach start\|stop\|status`. Rule-based. Handles NXML full text. |
| `reach-modern` | REACH built from clulab/reach master, on port 8081 (`--modern`). Needs `SBT_OPTS=-Xmx10g` and is about twice as slow per paper. |
| `trips` | TRIPS/DRUM web service at IHMC. Text only, one sentence or abstract per call. |

## Outputs

| File | Contents |
|---|---|
| `statements.json` | Assembled INDRA Statements (`stmts_to_json`): all agents with their `db_refs`, modification conditions, and every `Evidence` object. The other files are derived from this one. |
| `findings.tsv` | One row per statement: `type, subject_text, subject_grounding, object_text, object_grounding, detail, belief, evidence_sentence, pmid, source_api`. Groundings are `NAMESPACE:ID`, preferring HGNC, UniProt, ChEBI and MeSH over the raw text. `detail` holds residue and position for modifications and activity type for `Activation` and `Inhibition`. |
| `report.html` | INDRA's `HtmlAssembler` output: statements grouped with their supporting sentences and links to the source article. |
| `summary.md` | Counts by statement type, grounding coverage, the ten most frequent entities, reader used, and wall-clock time. |

Example `findings.tsv` rows:

| type | subject_text | subject_grounding | object_text | object_grounding | detail | belief | evidence_sentence |
|---|---|---|---|---|---|---|---|
| Phosphorylation | MEK1 | HGNC:6840 | ERK2 | HGNC:6871 | S:222 | 0.87 | MEK1 phosphorylates ERK2 on Ser222. |
| Activation | EGF | HGNC:3229 | EGFR | HGNC:3236 | activity | 0.65 | EGF activates EGFR. |

## Mapping INDRA statements to triples

| Triple element | INDRA |
|---|---|
| predicate | Statement type (`Phosphorylation`, `Activation`, `Complex`, and about 40 others) |
| subject, object | `stmt.agent_list()`, in role-named slots (`enz`/`sub`, `subj`/`obj`, or `members`) |
| identifier | `Agent.db_refs`, e.g. `{'HGNC': '6840', 'UP': 'Q02750', 'TEXT': 'MEK1'}` |
| qualifiers | Partial: `residue`, `position`, `Agent.mods`, `mutations`, `bound_conditions`. There is no general qualifier slot. |
| supporting quote | `Evidence.text`, with `pmid`, `source_api` and `annotations` |
| confidence | `stmt.belief`, a ranking score based on source reliability and the number of independent evidences |
| deduplication | `run_preassembly`, which merges duplicate and refinement-related statements and recomputes belief |

Limitations of INDRA's output:

- Statement types are INDRA's own vocabulary. There is no Biolink predicate or qualifier mapping.
- REACH and TRIPS are rule-based or hand-built parsers. They extract clean canonical sentences well and miss hedged, long-range, and cross-sentence claims, so recall is the weak axis.
- A Statement has no polarity field. `Evidence.epistemics` may flag an individual evidence as negated or hypothetical.
- One entity may be grounded to HGNC in one statement and UniProt in another.
- Belief is a ranking score and is not a calibrated probability.
- Evidence is a sentence and a PMID, with no section, offset, or figure provenance.

## Benchmark

```bash
indra-eval corpus build --n 50 --seed 42                               # sample papers from SIGNOR PMIDs with PMC OA full text
indra-eval benchmark --reader reach --corpus benchmark/corpus.json     # writes benchmark/reach/{scores.json,per_paper.tsv,report.md}
```

The benchmark needs the SIGNOR relation tables in `signor_landing_data/`, which are not included. See [`signor_landing_data/README.md`](signor_landing_data/README.md). SIGNOR is a sample of the signaling literature and the results do not generalize to other subfields.

## SIGNOR demo

`indra-eval signor` (or `python -m indra_eval.signor_demo`) loads SIGNOR through INDRA's database reader and writes `runs/signor/statements.json` and `runs/signor/type_counts.tsv`. It downloads from signor.uniroma2.it, which was unreachable in August 2026. When the site is down the command times out after 60 seconds with an error naming the download URLs. To run offline, pass hand-downloaded files: `uv run python -m indra_eval.signor_demo signor_all_data.tsv signor_complexes.tsv`.

## Troubleshooting

- **First REACH read is slow.** JVM start-up and model loading under emulation take 5 to 15 minutes. `docker logs` shows progress.
- **404 from the REACH container.** REACH 1.4 takes the text as a query parameter (`POST /api/text?text=...`), not a request body. Use `/api/uploadFile` for NXML.
- **Container exits or stops answering.** Run `indra-eval reach status`, then `reach stop` and `reach start`. Large documents can exhaust JVM memory. Raise the Docker memory limit or read a smaller input.
- **JVM crashes on Apple Silicon.** Old Java 8 images can segfault under Docker's QEMU emulation. Enabling `UseVirtualizationFrameworkRosetta` in Docker Desktop's settings fixes it.
- **`Could not import jnius` warning.** Harmless. INDRA's offline JVM reading path is unavailable and the HTTP service is used.

## License

MIT. See `LICENSE`. INDRA is BSD licensed; SIGNOR data carries its own terms.
