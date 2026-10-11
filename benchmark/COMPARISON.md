# Reader decision — REACH v1.4 vs modern (clulab master), 2026-08-20

The benchmark decides the default reader.

Corpus: 50 SIGNOR-referenced PMC OA papers (`benchmark/corpus.json`, seed 42), scored under
the scoring adjustments (see docs/SCORING.md): recall over the 82 matchable ground-truth rows (62 SIGNOR-native
rows excluded by construction), mechanism-aware matching for Modification statements.

| metric | reach (v1.4) | reach-modern (master) |
|---|---|---|
| recall (matchable rows) | **0.378** (31/82) | 0.342 (28/82) |
| recall (all 144 rows, secondary) | 0.215 | 0.194 |
| precision, strict | **0.029** | 0.023 |
| precision, grounded-only | **0.071** | 0.057 |
| F1 (strict) | **0.054** | 0.043 |
| TP (core + mechanism) | 31 (23+8) | 28 |
| papers completed | 50/50, 0 failures | 50/50, after 4 OOM retries with `-Xmx10g` |
| ~read time / paper (emulated) | ~45 s | ~90–120 s |

## Decision: the default reader stays `reach` (v1.4)

- Modern wins nothing measurable: every headline metric is level or slightly worse. The 3-TP
  recall gap at n=50 is within noise — read it as "no evidence of improvement", not "v1.4 is
  better at extraction".
- The decisive margin is operational. Modern master needs DyNet, whose native library ships
  x86_64-only (no arm64 — a native build SIGSEGVs the JVM on first read), boots via `sbt` in
  the container, OOM'd (docker exit 137) on 4 of 50 papers until given `SBT_OPTS=-Xmx10g`,
  and reads ~2× slower under the same emulation.
- Modern stays fully installable and benchmarkable (`indra-eval reach start --modern`,
  `indra-eval benchmark --reader reach-modern`) — rerun this comparison when upstream changes.

## Standing caveats (from the ground-truth addendum)

- Precision against SIGNOR is structurally deflated for any reader: SIGNOR curates 1–2 claims
  per paper, full-text extraction produces dozens. An unmatched extraction is usually
  un-curated, not wrong. Recall is the primary signal here.
- SIGNOR is a signaling/PTM-subfield sample; these numbers do not predict performance on the
  broader biomedical literature.
- Negation recall is untestable against this ground truth (zero negated rows).
