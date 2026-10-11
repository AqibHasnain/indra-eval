# indra-eval benchmark — reader `reach` vs SIGNOR

- corpus: `benchmark/corpus.json` (limit=None)
- papers scored: **50** of 50 requested (0 failed, 0 skipped)
- wall-clock: 89.1s

## Headline (micro-averaged, strict precision)

| metric | value |
|---|---|
| precision | 2.9% |
| recall | 37.8% |
| F1 | 5.4% |
| TP / FP / FN | 31 / 1039 / 51 |
| of TP: core-key / mechanism-aware | 23 / 8 |

Secondary: recall over **all** usable SIGNOR rows, including the 62 SIGNOR-native rows no CURIE-matched extraction could ever hit, is 21.5% (31/144).

### The two scoring adjustments (applied identically to every reader)

1. **SIGNOR-native entities excluded** — a GT row whose subject or object id starts with `SIGNOR` (complexes, protein families, phenotypes, stimuli) is dropped from the recall denominator, because no UniProt-grounded candidate can ever match it. Here: 62 of 144 rows.
2. **Mechanism-aware Modification match** — SIGNOR curates "PP1a dephosphorylates KAP1" as EFFECT=(activity, increased) + MECHANISM=dephosphorylation, while a correct extraction is `Dephosphorylation(PP1, KAP1)` -> (phosphorylation, decreased). A Modification candidate therefore also matches on `(subject_id, object_id)` when the SIGNOR MECHANISM maps to the same PTM family with a consistent direction. Here: 8 of 31 TPs came from this rule.

## Precision variants

| variant | precision | F1 |
|---|---|---|
| strict (ungrounded extractions count as FP) | 2.9% | 5.4% |
| grounded-only (both agents resolved to UniProt) | 7.1% | 12.0% |

## Denominators

| quantity | count |
|---|---|
| SIGNOR rows with usable EFFECT | 144 |
| of those, matchable — `n_gt_matchable`, the headline recall denominator | 82 |
| of those, SIGNOR-native and unmatchable — `n_gt_unmatchable` (adjustment 1) | 62 |
| SIGNOR rows skipped (unusable EFFECT) | 0 |
| candidate tuples from statements | 1540 |
| of those, SIGNOR-mappable types | 1070 |
| of those, both ids resolved | 435 |
| unmappable statement types (excluded from precision) | 9 |

### Entity id resolution modes (per candidate)

| mode | count |
|---|---|
| ungrounded | 1083 |
| up-direct | 448 |
| unmappable-type | 9 |

## Recall by SIGNOR mechanism

| mechanism | gt rows | matched | recall |
|---|---|---|---|
| phosphorylation | 35 | 20 | 57.1% |
| (none) | 16 | 0 | 0.0% |
| transcriptional regulation | 10 | 1 | 10.0% |
| dephosphorylation | 6 | 4 | 66.7% |
| binding | 5 | 4 | 80.0% |
| post transcriptional regulation | 4 | 0 | 0.0% |
| ubiquitination | 3 | 2 | 66.7% |
| relocalization | 2 | 0 | 0.0% |
| chemical inhibition | 1 | 0 | 0.0% |

## Precision by INDRA statement type

| statement type | candidates | matched | precision |
|---|---|---|---|
| Activation | 334 | 9 | 2.7% |
| Inhibition | 225 | 8 | 3.6% |
| Complex | 187 | 0 | 0.0% |
| Phosphorylation | 120 | 7 | 5.8% |
| IncreaseAmount | 81 | 2 | 2.5% |
| DecreaseAmount | 48 | 4 | 8.3% |
| Dephosphorylation | 48 | 1 | 2.1% |
| Ubiquitination | 14 | 0 | 0.0% |
| Deubiquitination | 6 | 0 | 0.0% |
| Sumoylation | 4 | 0 | 0.0% |
| Methylation | 2 | 0 | 0.0% |
| Desumoylation | 1 | 0 | 0.0% |

## Per paper

| pmid | gt matchable | gt unmatchable | candidates | tp | tp (mech) | fp | fn | P (strict) | R |
|---|---|---|---|---|---|---|---|---|---|
| 26195670 | 1 | 0 | 15 | 1 | 0 | 11 | 0 | 8.3% | 100.0% |
| 29179460 | 2 | 0 | 70 | 1 | 0 | 36 | 1 | 2.7% | 50.0% |
| 19287380 | 3 | 6 | 33 | 0 | 0 | 30 | 3 | 0.0% | 0.0% |
| 29048339 | 0 | 30 | 13 | 0 | 0 | 13 | 0 | 0.0% | 0.0% |
| 27206658 | 1 | 0 | 48 | 1 | 1 | 31 | 0 | 3.1% | 100.0% |
| 29156836 | 1 | 0 | 35 | 1 | 1 | 21 | 0 | 4.5% | 100.0% |
| 38111333 | 1 | 0 | 16 | 0 | 0 | 11 | 1 | 0.0% | 0.0% |
| 24150225 | 1 | 0 | 3 | 0 | 0 | 3 | 1 | 0.0% | 0.0% |
| 25097229 | 0 | 4 | 15 | 0 | 0 | 10 | 0 | 0.0% | 0.0% |
| 23400999 | 1 | 0 | 11 | 1 | 0 | 9 | 0 | 10.0% | 100.0% |
| 24470334 | 2 | 4 | 28 | 2 | 0 | 20 | 0 | 9.1% | 100.0% |
| 28381988 | 1 | 0 | 12 | 0 | 0 | 11 | 1 | 0.0% | 0.0% |
| 26449471 | 1 | 0 | 22 | 0 | 0 | 12 | 1 | 0.0% | 0.0% |
| 27966653 | 1 | 0 | 26 | 1 | 0 | 19 | 0 | 5.0% | 100.0% |
| 24145169 | 0 | 1 | 64 | 0 | 0 | 40 | 0 | 0.0% | 0.0% |
| 22317922 | 1 | 0 | 60 | 1 | 0 | 27 | 0 | 3.6% | 100.0% |
| 25085254 | 0 | 1 | 14 | 0 | 0 | 10 | 0 | 0.0% | 0.0% |
| 19333390 | 1 | 0 | 12 | 0 | 0 | 9 | 1 | 0.0% | 0.0% |
| 31805502 | 3 | 0 | 14 | 0 | 0 | 11 | 3 | 0.0% | 0.0% |
| 29794136 | 1 | 0 | 25 | 1 | 0 | 22 | 0 | 4.3% | 100.0% |
| 31690749 | 1 | 0 | 21 | 1 | 0 | 14 | 0 | 6.7% | 100.0% |
| 27857196 | 1 | 0 | 47 | 1 | 1 | 29 | 0 | 3.3% | 100.0% |
| 19329994 | 1 | 0 | 54 | 0 | 0 | 31 | 1 | 0.0% | 0.0% |
| 30026574 | 1 | 2 | 24 | 1 | 0 | 14 | 0 | 6.7% | 100.0% |
| 20424263 | 1 | 0 | 61 | 0 | 0 | 40 | 1 | 0.0% | 0.0% |
| 21148297 | 3 | 6 | 39 | 0 | 0 | 27 | 3 | 0.0% | 0.0% |
| 33310066 | 2 | 0 | 50 | 2 | 0 | 37 | 0 | 5.1% | 100.0% |
| 25368549 | 1 | 0 | 27 | 1 | 0 | 22 | 0 | 4.3% | 100.0% |
| 31731478 | 1 | 0 | 20 | 0 | 0 | 14 | 1 | 0.0% | 0.0% |
| 28821708 | 1 | 0 | 14 | 1 | 0 | 10 | 0 | 9.1% | 100.0% |
| 23967200 | 1 | 0 | 58 | 0 | 0 | 29 | 1 | 0.0% | 0.0% |
| 24294527 | 6 | 0 | 12 | 0 | 0 | 9 | 6 | 0.0% | 0.0% |
| 32570757 | 1 | 0 | 17 | 0 | 0 | 15 | 1 | 0.0% | 0.0% |
| 25945589 | 2 | 0 | 22 | 0 | 0 | 13 | 2 | 0.0% | 0.0% |
| 25675981 | 0 | 5 | 20 | 0 | 0 | 20 | 0 | 0.0% | 0.0% |
| 36574265 | 3 | 0 | 27 | 1 | 1 | 24 | 2 | 4.0% | 33.3% |
| 34162842 | 0 | 1 | 28 | 0 | 0 | 23 | 0 | 0.0% | 0.0% |
| 20832753 | 1 | 0 | 27 | 1 | 0 | 19 | 0 | 5.0% | 100.0% |
| 28060952 | 1 | 0 | 56 | 1 | 0 | 34 | 0 | 2.9% | 100.0% |
| 26774286 | 3 | 2 | 47 | 2 | 2 | 32 | 1 | 5.9% | 66.7% |
| 30377223 | 1 | 0 | 42 | 1 | 0 | 28 | 0 | 3.5% | 100.0% |
| 19536134 | 2 | 0 | 43 | 2 | 1 | 21 | 0 | 8.7% | 100.0% |
| 33684228 | 1 | 0 | 32 | 1 | 0 | 20 | 0 | 4.8% | 100.0% |
| 18541707 | 1 | 0 | 29 | 1 | 0 | 26 | 0 | 3.7% | 100.0% |
| 34417472 | 1 | 0 | 38 | 1 | 0 | 32 | 0 | 3.0% | 100.0% |
| 22394684 | 1 | 0 | 32 | 0 | 0 | 29 | 1 | 0.0% | 0.0% |
| 26112872 | 18 | 0 | 16 | 0 | 0 | 7 | 18 | 0.0% | 0.0% |
| 32764831 | 1 | 0 | 32 | 1 | 0 | 18 | 0 | 5.3% | 100.0% |
| 20881056 | 2 | 0 | 26 | 1 | 1 | 15 | 1 | 6.2% | 50.0% |
| 20686657 | 1 | 0 | 43 | 1 | 0 | 31 | 0 | 3.1% | 100.0% |

## Honest-reporting caveat

> SIGNOR is a curated signaling-subfield sample (mostly human protein signaling, hand-picked sentences). These numbers describe agreement with that slice of the literature only and do NOT generalize to the full literature or to non-signaling claims.

Matching uses the frozen core key `(subject_id, object_id, aspect, direction)` within a PMID; SIGNOR rows with a bare up/down-regulates EFFECT wildcard the aspect, and `form complex` rows match INDRA `Complex` statements on the unordered id pair.
