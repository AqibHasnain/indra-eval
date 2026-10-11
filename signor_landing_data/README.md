# SIGNOR data

The benchmark reads SIGNOR relations from `signor_human.tsv` in this directory (`signor_mouse.tsv` and `signor_rat.tsv` are optional). The files are not included in the repository.

Download the per-species relation tables from https://signor.uniroma2.it (Download page) and save them without a header row as `signor_human.tsv`, `signor_mouse.tsv`, and `signor_rat.tsv`. The expected format is 28 tab-separated positional columns, listed in `indra_eval/signor_gt.py`. SIGNOR is distributed under CC BY-SA 4.0; check the site for current terms.

The benchmark results in `benchmark/` were produced from a SIGNOR snapshot downloaded in August 2026.
