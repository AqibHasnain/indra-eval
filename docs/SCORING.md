# Benchmark ground truth and scoring

## Ground truth (`indra_eval/signor_gt.py`)

- Input: `signor_landing_data/signor_{human,mouse,rat}.tsv` (see `signor_landing_data/README.md`), headerless with 28 positional columns:
  ENTITY_A, TYPE_A, ID_A, DATABASE_A, ENTITY_B, TYPE_B, ID_B, DATABASE_B, EFFECT, MECHANISM,
  RESIDUE, SEQUENCE, TAX_ID, CELL_DATA, TISSUE_DATA, MODULATOR_COMPLEX, TARGET_COMPLEX,
  MODIFICATION_A, MODASEQ, MODIFICATION_B, MODBSEQ, PMID, DIRECT, NOTES, ANNOTATOR, SENTENCE,
  SIGNOR_ID, SCORE.
- Scoring uses human rows only; the loader reads any species file.
- Normalized ground-truth record: `{pmid, subject_id (UniProt or SIGNOR-native), subject_name,
  object_id, object_name, aspect, direction, mechanism, sentence, signor_id}` where EFFECT maps:
  "up-regulates*" → direction=increased, "down-regulates*" → decreased; "…activity" → aspect=activity,
  "…quantity…" → aspect=abundance, bare up/down-regulates → aspect=None (wildcard on match);
  "form complex" → predicate=in_complex_with (aspect/direction None); "unknown" EFFECT rows are
  excluded from scoring denominators (recorded as skipped).

## Scoring

- Core match key: `(subject_id, object_id, aspect, direction)`, matched **within a PMID**.
  aspect=None on the SIGNOR side matches any candidate aspect. Complex statements match
  `form complex` rows on `(subject_id, object_id)` unordered.
- INDRA statement → candidate tuple: Activation→(activity,increased), Inhibition→(activity,decreased),
  IncreaseAmount→(abundance,increased), DecreaseAmount→(abundance,decreased),
  Phosphorylation/other Modifications→(that PTM, increased; De-mods→decreased), Complex→in_complex_with.
  Statements with unmappable types are excluded from precision denominators but counted/reported.
- Entity id: prefer agent `db_refs['UP']`; else map HGNC→UniProt via
  `indra.databases.hgnc_client.get_uniprot_id`; FPLX/ungrounded → unmatchable (counts against
  precision as extracted-but-unmatched only in the strict variant; report both strict and
  grounded-only precision).
- Metrics: per-paper and aggregate precision/recall/F1 on the core key, plus slices by SIGNOR
  MECHANISM and by INDRA statement type, plus secondary-dimension agreement where computable.
  SIGNOR recall denominator = that paper's human SIGNOR rows with usable EFFECT.
- Reports state that SIGNOR is a sample of the signaling subfield and that results do not generalize to the full literature.

## Scoring adjustments

Two adjustments, found by hand-tracing false negatives on the 50-paper REACH 1.4 run. Both apply identically to every reader.

1. **SIGNOR-native entities are out of the CURIE-matching game**: a GT row whose subject_id or object_id starts with `SIGNOR` (complexes,
   protein families, phenotypes, stimuli) is excluded from the recall denominator and counted as
   `n_gt_unmatchable` per paper + aggregate. Measured on the current corpus: 62/144 rows.
   Headline recall = TP / matchable rows; all-rows recall stays as a secondary line.
2. **Mechanism-aware match for Modifications**: a candidate Modification tuple also matches a GT
   row on the same `(subject_id, object_id)` when SIGNOR's MECHANISM maps to the same PTM family
   with consistent direction, via a small curated map: phosphorylation↔(phosphorylation, increased),
   dephosphorylation↔(phosphorylation, decreased), (poly/mono)ubiquitination↔(ubiquitination,
   increased), deubiquitination↔(ubiquitination, decreased), acetylation/deacetylation and
   methylation/demethylation likewise. Rationale: SIGNOR curates "PP1α dephosphorylates KAP1" as
   EFFECT=(activity, increased)+MECHANISM=dephosphorylation while a correct extraction is
   `Dephosphorylation(PP1, KAP1)` → these are the same claim. Mechanism-level TPs are merged into
   headline TP and ALSO reported separately (`tp_mechanism`) so the lift is visible.

## Benchmark harness

- `indra-eval corpus build --n 50 --seed 42` → `benchmark/corpus.json`: paper-level manifest
  {pmid, pmcid} sampled deterministically from human SIGNOR PMIDs that have PMC OA full text
  (resolve via NCBI idconv batch API, then efetch spot-verify; record the measured OA-overlap rate).
- `indra-eval benchmark --reader reach|reach-modern --corpus benchmark/corpus.json [--limit N]` →
  reuses pipeline.run per paper (cache: skip papers whose run dir already has statements.json for
  that reader), scores, writes `benchmark/<reader>/{scores.json, per_paper.tsv, report.md}`.
- The corpus manifest and scores are committed. Per-paper run directories are gitignored.
