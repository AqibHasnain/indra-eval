# Glossary

Terms you will hit reading indra-eval output or [HOW-IT-WORKS.md](HOW-IT-WORKS.md).
Examples are real, from `runs/PMC3898398/` (PMC3898398 / PMID 24360018) unless
noted.

---

## INDRA data model

**INDRA** — Integrated Network and Dynamical Reasoning Assembler. A Python library
(1.24.0 here) that reads biology from text and from curated databases into one
shared statement model, then assembles it. This repo uses two slices of it: the
reader clients (`indra.sources.*`) and the assembly tools
(`indra.tools.assemble_corpus`).

**Statement** — INDRA's unit of extracted knowledge, and the closest thing to a
Biolink-style triple with evidence. A Python object with a **type** (which acts as the
predicate), **agents in role-named slots**, type-specific detail fields, an
**evidence** list, and a **belief** score. ~40 types exist; eleven appear in
PMC3898398.

**Statement type** — the class name, and effectively the predicate vocabulary:
`Phosphorylation`, `Deacetylation`, `Ribosylation`, `Sumoylation`, `Activation`,
`Inhibition`, `Complex`, `IncreaseAmount`, `DecreaseAmount`, `Translocation`, and
so on. INDRA's own closed vocabulary — not Biolink, with no mapping to it provided.

**Agent** — one participant in a Statement: an INDRA `name` (canonical label), a
`db_refs` dict of groundings, and optional state (`mods`, `mutations`,
`bound_conditions`, `activity`, `location`).

**Role slot** — the attribute an agent sits in, which varies by type. Modifications
use `enz` (enzyme) and `sub` (substrate); regulations use `subj` and `obj`;
`Complex` uses `members`, a list that can be longer than two. `stmt.agent_list()`
flattens all of them into positional order.

**`db_refs`** — an Agent's grounding dictionary, mapping namespace to id. Plural on
purpose: `{"EGID": "23411", "HGNC": "14929", "TEXT": "SIRT1", "UP": "Q96EB6"}` is
one agent with three real groundings plus the surface string. There is no single
canonical CURIE per agent; consumers pick one.

**`TEXT` (in `db_refs`)** — not a grounding. The literal string the reader saw in
the sentence. `findings.tsv` shows it in the `subject_text` / `object_text` columns
and deliberately excludes it from the grounding columns.

**Grounding** — attaching an entity mention to an identifier in a real namespace.
"Grounded" means `db_refs` has something other than `TEXT`; "ungrounded" means it
has only `TEXT`. Three of PMC3898398's 71 statements have an ungrounded agent
(`H4K16`, `H4K20me2`, `NAD+`).

**Grounding coverage** — the metric in `summary.md`: percentage of statements where
*every* agent is grounded. 87.3% for PMC3898398. It measures presence, not
correctness — the run also contains `CRY` grounded to `HGNC:18246`, which is the
gene *CRYL1*, not a cryptochrome.

**`map_grounding`** — `indra.tools.assemble_corpus.map_grounding`. Applies INDRA's
curated grounding map to correct and normalize `db_refs` after reading. The
pre-mapping values survive in `evidence.annotations.agents.raw_grounding`, so you
can diff before and after.

**`map_sequence`** — sanity-checks a modification's `residue`/`position` against
the actual protein sequence and fixes off-by-N site errors. Skipped with a note in
`summary.md` if it throws.

**Preassembly** — `run_preassembly`, INDRA's dedup-and-hierarchy step. Collapses
statements with the same **matches key** into one statement whose evidence list is
the union, computes the refinement hierarchy, and recomputes belief. This is where
90 raw REACH statements became 71 assembled ones.

**Matches key / `matches_hash`** — the identity of a Statement: type + agents in
roles + modification details, hashed. Two statements with the same hash are the
same statement and get merged. `Complex(CLOCK, BMAL1)` in this run has
`matches_hash 12643829116021084` and two evidences behind it.

**Refinement (`supports` / `supported_by`)** — the hierarchy preassembly builds
when one statement is strictly more specific than another (a statement about
`MAPK1` refines one about the `MAPK` family). `run_preassembly(...,
return_toplevel=True)` returns only the most specific ones; the general ones are
reachable through these links.

**Belief** — a float on the Statement, computed from source reliability and
evidence count. With INDRA's default priors for REACH (systematic error 0.05,
random error 0.3), `belief = 1 − (0.05 + 0.3^n)`: one evidence → 0.65, two → 0.86.
Exactly the two values in this run. A **ranking signal, not a calibrated
probability**.

**Evidence** — one supporting mention: `text` (the sentence), `pmid`, `text_refs`,
`source_api`, `annotations`, `epistemics`. A Statement holds a list of them.
`findings.tsv` shows only the first.

**`source_api`** — which reader or database produced an evidence: `reach`, `trips`,
`signor`, …. The key belief inputs come from this field.

**`epistemics`** — per-evidence modality flags: `direct`, `negated`, `hypothesis`.
Note *per evidence* — a Statement has no polarity field, so a negated mention and
an asserted one produce the same Statement type and can be merged together.

**`found_by`** — in `evidence.annotations`: the name of the reader rule that fired.
`Phosphorylation_syntax_1a_verb`, `binding_complex_A_B`. A blunt reminder that
REACH is a grammar, not a model.

**`coords`** — in `evidence.annotations.agents`: character offsets of each agent
*within the evidence sentence*, e.g. `[[0, 4], [26, 31]]`. The only positional
provenance INDRA carries. There is no offset into the source document.

**`residue` / `position`** — modification site fields on modification statements:
`residue="T", position="344"` for `Phosphorylation(AMPK, SIRT1)`. Rendered as
`detail=residue=T;position=344` in `findings.tsv`.

**`obj_activity` / `subj_activity`** — which activity a regulation statement acts
on. Almost always the generic `activity` in this run.

**`stmts_to_json`** — the serializer behind `statements.json`. Full fidelity: every
agent, every `db_refs` entry, every evidence, all annotations. Every other output
file is a lossy view of it.

**`HtmlAssembler`** — `indra.assemblers.html.HtmlAssembler`, which renders
statements as a browsable evidence-linked page. Produces `report.html` (629 KB for
PMC3898398) — the fastest way for a human to judge a batch.

---

## Readers and their formats

**Reader** — a program that turns sentences into structured events. Both readers
here are external services and both **predate the LLM era**.

**REACH** — Reading and Assembling Contextual and Holistic mechanisms from text.
A rule/grammar-based reader over dependency parses, from clulab (Arizona). The only
reader here that consumes NXML full text, and the reader most published
INDRA-derived corpora were built with. The hosted Arizona web service INDRA's docs
point at is **dead**; this repo runs it locally in docker.

**REACH v14** — `pathwaycommons/reach-docker:latest`, REACH 1.4, 2018-era, amd64
only, container `indra-reach`, port 8080. Runs emulated on Apple Silicon. **The
default reader**.

**REACH modern** — `reach-modern:latest`, built from clulab/reach master, container
`indra-reach-modern`, port 8081. Intended to be native arm64, but its DyNet native
library is x86_64-only, so it ends up under Rosetta anyway; it also needs
`SBT_OPTS=-Xmx10g` after OOMing (docker exit 137) on 4 of the 50 benchmark papers,
and runs ~2× slower per paper. Scored slightly lower than v1.4 (recall 0.342 vs
0.378) but within noise at n=50 — it lost on operations, not accuracy. Still
installable via `--modern` / `--reader reach-modern`.

**DyNet** — the neural-network C++ library REACH's newer models depend on. Its native
library ships x86_64-only, which is why "modern REACH on arm64" is not actually
native.

**`n_gt_unmatchable` / SIGNOR-native entity** — Scoring adjustment 1. A ground-truth row whose
`subject_id` or `object_id` starts with `SIGNOR` is a SIGNOR-internal complex, family,
phenotype, or stimulus with no UniProt accession, so nothing INDRA emits can ever
match it. Such rows are excluded from the recall denominator and counted here:
**62 of 144 rows** on this corpus. Headline recall is `TP / matchable rows`.

**Mechanism-aware match** — Scoring adjustment 2. A candidate Modification also matches a GT
row on `(subject_id, object_id)` when SIGNOR's `MECHANISM` maps to the same PTM
family with consistent direction (phosphorylation↔increased,
dephosphorylation↔decreased, and likewise for ubiquitination, acetylation,
methylation). It exists because SIGNOR records "PP1α dephosphorylates KAP1" as
`EFFECT=(activity, increased)` + `MECHANISM=dephosphorylation`, which is the same
claim as `Dephosphorylation(PP1, KAP1)`.

**`tp_core` / `tp_mechanism`** — the split of the headline true-positive count: hits
on the plain core key versus hits that needed the mechanism-aware rule. For v1.4,
31 TP = 23 core + 8 mechanism.

**`recall_all_rows`** — the secondary recall line, using all 144 GT rows as the
denominator instead of the 82 matchable ones. 0.215 for v1.4, 0.194 for modern.

**FRIES** — REACH's native output format: a JSON document with three parallel frame
collections, `sentences`, `entities`, and `events`. `runs/PMC3898398/reach_output.json`
holds 198 sentence frames, 765 entity mentions, and 197 event frames, which INDRA
converted into 90 raw Statements.

**TRIPS / DRUM** — a hand-built deep semantic parser, hosted at IHMC (alive as of
2026-08-20). Text only, deeper parses, but practically one sentence or one abstract
per call. Took 30.8s for `"MEK phosphorylates ERK."`.

**`/api/text` and `/api/uploadFile`** — REACH's two endpoints. `/api/text` takes
`text` and `output` as **query parameters on a POST with an empty body**; a form or
JSON body returns 404. `/api/uploadFile` takes NXML as a multipart upload.

**vsyscall crash** — the failure mode of running the 2018 amd64 image under Docker's
qemu emulation on Apple Silicon: the Java 8 JVM SIGSEGVs at the vsyscall page
(`pc=0xffffffffff600400`) and the container dies silently. Fixed by enabling
`UseVirtualizationFrameworkRosetta` in Docker's `settings-store.json`.

**jnius** — the Java-Python bridge INDRA uses for *offline* JVM-based reading. The
`Could not import jnius` warning is harmless here: we read over HTTP.

---

## Identifiers and namespaces

**CURIE** — Compact URI: `PREFIX:LOCAL_ID`, e.g. `HGNC:14929`. `findings.tsv`
prints groundings CURIE-style, but note the quirk that CHEBI ids already carry their
prefix, so you get `CHEBI:CHEBI:16761`.

**HGNC** — HUGO Gene Nomenclature Committee, the authority for human gene symbols
and ids. `SIRT1` → `HGNC:14929`. Fifty of 71 statements in this run have an
HGNC-grounded subject.

**UP (UniProt)** — protein accessions. `SIRT1` → `UP:Q96EB6`. SIGNOR keys on
UniProt, which is why the benchmark prefers `db_refs['UP']` and falls back to
`hgnc_client.get_uniprot_id`.

**EGID** — Entrez Gene id, carried alongside HGNC. `SIRT1` → `EGID:23411`.

**CHEBI** — Chemical Entities of Biological Interest: small molecules and
metabolites. `ADP` → `CHEBI:CHEBI:16761`.

**MESH** — Medical Subject Headings. In this run it mostly grounds *biological
processes*, which is why it is a problem for scoring: `transcription` →
`MESH:D014158`, `apoptosis` → `MESH:D017209`, `necrosis` → `MESH:D009336`. Twelve
of 71 statements have a MeSH-grounded object.

**FPLX (FamPlex)** — INDRA's own ontology of protein **families and named
complexes**, for entities that have no single gene: `FPLX:AMPK`, `FPLX:CK2`,
`FPLX:NFkappaB`, `FPLX:Histone_H1`, `FPLX:Caspase`. Biologically the right answer
and *unmatchable* against a UniProt-keyed ground truth.

**GO / DOID / HP / EFO / PUBCHEM / UPPRO / NCIT / ECCODE** — other namespaces that
appear in `db_refs` or in the `outputs.py` priority list. `AMPK` for instance
carries `ECCODE:2.7.11.31` and `NCIT:C94701` alongside `FPLX:AMPK`.

**Grounding priority list** — the fixed order in `indra_eval/outputs.py` used to
collapse a multi-namespace `db_refs` into one displayed CURIE: HGNC, UP, UPPRO,
FPLX, CHEBI, PUBCHEM, MESH, GO, MIRBASE, EFO, DOID, HP, INDRA_ACTIVITIES, then any
non-`TEXT` entry.

---

## Papers and fetching

**PMID** — PubMed identifier, e.g. `24360018`. INDRA's provenance key; it appears
on every `Evidence`.

**PMCID** — PubMed Central identifier, e.g. `PMC3898398`. What you need for *full
text*; a PMID only gets you an abstract.

**NXML** — the JATS XML flavor PMC serves full text as. `runs/PMC3898398/input.nxml`
is 153 KB of it. REACH consumes it directly; TRIPS needs it flattened with
`pmc_client.extract_text()` first.

**Front matter** — the `<front>` block of an NXML document, containing the
`<article-id>` tags. This repo parses the PMID out of it
(`<article-id pub-id-type="pmid">24360018</article-id>`) rather than calling NCBI's
id converter, which 403s intermittently. The *first* such tag is the article's own;
later ones belong to the reference list.

**efetch** — NCBI's E-utilities fetch endpoint. `db=pmc&rettype=xml` serves full
text for papers PMC's OAI-PMH endpoint refuses, so `fetch.py` tries it first and
keeps `pmc_client.get_xml` (OAI-PMH, OA-subset-only, rate-limited) as fallback.

**PMC OA subset** — the open-access portion of PMC. Governs whether a paper can be
in the benchmark corpus at all: the corpus is built from SIGNOR PMIDs that *also*
have PMC OA full text.

**`XREF_BIBR`** — the placeholder left where an inline citation was stripped before
reading. Its presence in an evidence sentence means the claim is attributed to a
*cited* paper, while INDRA stamps the evidence with the *reading* paper's PMID.
Endemic in a review article like PMC3898398.

---

## Ground truth and scoring

**SIGNOR** — a manually curated database of causal signaling interactions from
`signor.uniroma2.it`. Each row is one curator's assertion *plus the PMID they read
it in*, which is what makes it usable as per-paper ground truth. The site was
unreachable all day 2026-08-20, so this repo scores against landed dumps in
`signor_landing_data/`.

**`EFFECT`** — SIGNOR's relation column: `up-regulates activity`,
`down-regulates quantity by destabilization`, bare `up-regulates`, `form complex`,
`unknown`. `signor_gt.map_effect` normalizes it into aspect + direction; `unknown`
rows are excluded from scoring denominators (1,083 of 42,476 human rows).

**Aspect** — *what* about the object changed: `activity` or `abundance`. `None`
means the curator didn't say, and acts as a **wildcard** that matches any candidate
aspect (10,667 human rows are bare up/down-regulates).

**Direction** — *which way*: `increased` or `decreased`.

**Core match key** — `(subject_id, object_id, aspect, direction)`, matched **within
a single PMID**. The tuple both sides are reduced to. `Complex` statements instead
match `form complex` rows on `(subject_id, object_id)`, unordered.

**`in_complex_with`** — the predicate used for SIGNOR `form complex` rows and INDRA
`Complex` statements, which carry no aspect or direction. 2,858 usable human rows.

**Unmatchable** — an INDRA statement whose agents cannot be reduced to UniProt ids:
`FPLX` families, `MESH` processes, ungrounded agents. Only **35 of 71** statements
in PMC3898398 have both agents at HGNC/UniProt level.

**Strict vs grounded-only precision** — the two precision numbers the scoring spec
requires reporting, because the choice moves the result a lot. *Strict* counts
unmatchable extractions against you; *grounded-only* excludes them from the
denominator. For v1.4 on the 50-paper corpus: 0.029 strict vs 0.071 grounded-only —
a 2.5× spread, because only 435 of 1540 candidates were grounded well enough to
carry a UniProt id.

**Precision (here)** — of the reader's representable statements for a paper, the
fraction hitting a SIGNOR row for the same PMID.

**Recall (here)** — of that paper's usable human SIGNOR rows, the fraction the
reader found. The denominator is SIGNOR's, so this is "how much of what a curator
wrote down did the machine also get".

**`MECHANISM`** — SIGNOR's mechanism column (`binding`, `phosphorylation`, …). Used
as a reporting slice, so you can see whether a reader is good at everything or just
at phosphorylation.

**Signaling-subfield sample** — the mandatory caveat. SIGNOR curates
post-translational signaling, not a sample of the biomedical literature. Scores on
it do not generalize, and every report must say so.

**`benchmark/corpus.json`** — the paper-level manifest, `{pmid, pmcid}` pairs
sampled deterministically (`--n 50 --seed 42`) from human SIGNOR PMIDs that have
PMC OA full text. Committed; the per-paper run directories are not.

---

## Correspondence to a Biolink-style triple

| Triple element | INDRA |
|---|---|
| predicate | Statement type (`Phosphorylation`, `Activation`, …) — INDRA's own vocabulary, no Biolink mapping |
| subject / object | `stmt.agent_list()`, drawn from `enz`/`sub`, `subj`/`obj`, or `members` |
| CURIE | one entry chosen out of `Agent.db_refs`, which holds several |
| qualifiers | partial: `residue`/`position`, `obj_activity`, `Agent.mods`/`mutations`/`bound_conditions`. No general qualifier slot, no cell type, no tissue, no dose |
| evidence quote | `Evidence.text` — the sentence, with `pmid`, `source_api`, `annotations` |
| confidence | `stmt.belief` — ranking signal, not `P(true)` |
| dedup | preassembly: matches key → merged evidence → recomputed belief |
| document structure | absent. A sentence and a PMID, nothing else |
| polarity / negation | absent at statement level; `Evidence.epistemics.negated` only |
