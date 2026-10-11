# How indra-eval works

A teaching walkthrough of what happens between "here is a paper id" and "here are
71 statements". Every number, row, and
command below comes from files in this repo — mostly from `runs/PMC3898398/`,
a real REACH run over *SIRT1/PARP1 crosstalk: connecting DNA damage and
metabolism* (PMC3898398 / PMID 24360018).

New to the vocabulary? Read [GLOSSARY.md](GLOSSARY.md) alongside this.

---

## 1. The 30-second picture

```
  indra-eval read PMC3898398
            │
            ▼
  ┌─────────────────────┐
  │ 1. FETCH            │  PMC3898398 → NCBI efetch → input.nxml (153 KB)
  │  indra_eval/fetch.py│  PMID 24360018 parsed out of the NXML front matter
  └─────────┬───────────┘
            ▼
  ┌─────────────────────┐
  │ 2. READ             │  POST the NXML to local REACH in docker
  │  REACH  or  TRIPS   │  → reach_output.json (FRIES: 198 sentences,
  │                     │     765 entity mentions, 197 event frames)
  └─────────┬───────────┘
            ▼
  ┌─────────────────────┐
  │ 3. RAW STATEMENTS   │  INDRA converts FRIES frames → 90 raw Statements
  │  indra.sources.reach│  (Python objects: type + agents + evidence)
  └─────────┬───────────┘
            ▼
  ┌─────────────────────┐
  │ 4. GROUNDING        │  ac.map_grounding  — fix/normalize db_refs
  │    + SEQUENCE MAP   │  ac.map_sequence   — sanity-check residue/position
  └─────────┬───────────┘
            ▼
  ┌─────────────────────┐
  │ 5. PREASSEMBLY      │  ac.run_preassembly — dedup + belief
  │                     │  90 raw → 71 assembled; 9 of them get 2 evidences
  └─────────┬───────────┘
            ▼
  ┌───────────────────────────────────────────────┐
  │ 6. FOUR OUTPUTS in runs/PMC3898398/           │
  │  statements.json  findings.tsv                │
  │  report.html      summary.md                  │
  └─────────┬─────────────────────────────────────┘
            ▼
  ┌─────────────────────────────────────────────────────┐
  │ 7. BENCHMARK                                        │
  │  score the statements against curated SIGNOR rows   │
  │  for the same PMID → precision / recall / F1        │
  │  50 papers, seed 42: v1.4 recall 0.378, F1 0.054    │
  └─────────────────────────────────────────────────────┘
```

Stages 1–6 are what `indra-eval read` does today (`indra_eval/pipeline.py:16`).
Stage 7 is the SIGNOR benchmark, and it is **finished**: it ran over a 50-paper SIGNOR
corpus for both REACH variants and settled the default-reader question. Results and
the decision are in §5.

---

## 2. Stage by stage

### Stage 1 — Fetch

**In:** one CLI argument. **Out:** a string of NXML or plain text, plus a run id.

`fetch.resolve()` (`indra_eval/fetch.py:12`) branches on the shape of the string:

| argument | route | run id | content kind |
|---|---|---|---|
| `PMC3898398` | NCBI efetch `db=pmc&rettype=xml`, falling back to `pmc_client.get_xml` | `PMC3898398` | `nxml` (full text) |
| `32133153` | `pubmed_client.get_abstract` | `32133153` | `text` (abstract only) |
| `"MEK phosphorylates ERK."` | used verbatim | `text-<sha1[:8]>` | `text` |

Two things worth knowing:

**efetch, not OAI-PMH.** INDRA's own `pmc_client.get_xml` goes through PMC's
OAI-PMH endpoint, which serves the open-access subset only and rate-limits
aggressively. Plain efetch returns full text for papers OAI refuses, so `fetch.py`
tries efetch first and keeps `pmc_client` as the fallback.

**The PMID comes out of the paper, not out of a second API call.** NCBI's
unauthenticated id-conversion endpoint 403s intermittently. But the article's own
front matter already carries it:

```xml
<article-id pub-id-type="pmid">24360018</article-id>
```

`fetch.pmid_from_nxml()` regexes the *first* such tag out of the document
(`indra_eval/fetch.py:50`). In `runs/PMC3898398/input.nxml` the first match is
`24360018` — the article — and the ones after it belong to the reference list.
`fetch.pmid_for_pmcid()` (the network call) only runs if that fails.

**What can go wrong:** the paper isn't in PMC at all (`RuntimeError: no full-text
XML available…`); or efetch returns an error page, which is caught by the
`"<article" not in resp.text` check.

**See it yourself:**
```bash
head -c 400 runs/PMC3898398/input.nxml
grep -o 'pub-id-type="pmid">[0-9]*' runs/PMC3898398/input.nxml | head -1
```

### Stage 2 — Read

**In:** NXML or text. **Out:** reader-native JSON/XML, written into the run dir.

Two readers, both external services:

- **`--reader reach`** (default) — REACH 1.4 in a local docker container,
  `pathwaycommons/reach-docker:latest`, port 8080. Managed by
  `indra_eval/reach_service.py`. The **only** reader that eats NXML.
- **`--reader trips`** — the TRIPS/DRUM web service at IHMC. Text only; NXML is
  flattened with `pmc_client.extract_text()` first, and a note is recorded in
  `summary.md`. Practically limited to a sentence or an abstract per call.

The hosted Arizona REACH web service that INDRA's docs point at is **dead**. Local
docker is the only REACH.

**The protocol detail that costs everyone an hour.** REACH's `ApiServer` takes
`text` and `output` as **query parameters on a POST with an empty body**:

```
POST http://localhost:8080/api/text?text=MEK+phosphorylates+ERK.&output=fries
```

A form-encoded or JSON body gets a flat **404**. INDRA's client already does the
right thing (`requests.post(url, params=...)`), so the rule is: any hand-written
probe must match INDRA, not your instinct. NXML goes to a different endpoint,
`/api/uploadFile`, as a multipart upload.

**The Rosetta story, because it is instructive.** The 2018-era image is amd64-only.
Under Docker Desktop's default qemu emulation on Apple Silicon, its Java 8 JVM
SIGSEGVs at the vsyscall page (`pc=0xffffffffff600400`) — the container just dies,
with no useful message. Flipping `UseVirtualizationFrameworkRosetta: true` in
`~/Library/Group Containers/group.com.docker/settings-store.json` and restarting
Docker fixes it outright: the same paper that crashed under qemu read in 55s under
Rosetta. Old JVMs and qemu's vsyscall handling do not mix; Rosetta's does.

**Timings, measured in this repo:** first request after container start pays JVM
boot plus lazy NLP model load — **5–15 minutes**, and it is not hung. After that,
a full paper is fast: 62.5s for PMC3898398, 38.4s for PMC5356684 (see their
`summary.md`). TRIPS took 30.8s for a single sentence
(`runs/text-a0e9c285/summary.md`).

**What comes out.** `runs/PMC3898398/reach_output.json` is 2.0 MB of **FRIES** —
REACH's native output, three parallel frame collections:

| collection | frames | what it is |
|---|---|---|
| `sentences` | 198 | the sentences REACH segmented the paper into |
| `entities` | 765 | entity *mentions*, each with character offsets and candidate groundings |
| `events` | 197 | the extracted events — the things that become Statements |

**What can go wrong:** the service isn't up (`reach status` says `booting` or
`no-container`); the JVM OOMs on a large document (raise Docker Desktop's memory
limit); the reader legitimately finds nothing, which `pipeline.run` turns into an
explicit `RuntimeError` rather than an empty run directory.

**See it yourself:**
```bash
uv run indra-eval reach status
python3 -c "import json;d=json.load(open('runs/PMC3898398/reach_output.json'));print({k:len(v['frames']) for k,v in d.items()})"
```

### Stage 3 — Raw Statements

**In:** FRIES. **Out:** 90 Python `Statement` objects.

`indra.sources.reach` walks the FRIES event frames and instantiates INDRA
`Statement` subclasses — `Phosphorylation`, `Activation`, `Complex`, and ~40 more.
This is the step where a reader-specific format becomes INDRA's shared data model.
The 197 event frames collapse to 90 statements: not every event frame is a
relation INDRA models (some are entity-modification fragments, some are event
types with no Statement counterpart).

Each raw Statement already carries one `Evidence` object: the sentence, the PMID,
`source_api='reach'`, and REACH's own annotations — including `found_by`, the name
of the rule that fired. In our run you see rule names like
`Phosphorylation_syntax_1a_verb` and `binding_complex_A_B`. That string is a
useful debugging handle and a reminder of what REACH is: a grammar, not a model.

**What can go wrong here is the interesting part** — the reader's mistakes are
already baked in, and they survive to the TSV. Real rows from
`runs/PMC3898398/findings.tsv`:

| what happened | the row | the sentence |
|---|---|---|
| subject/object scrambled | `Deacetylation  c-MYC → c-MYC` | "…deacetylation of c-MYC by SIRT1 leads to the destabilization of c-MYC" — SIRT1 is the enzyme, not c-MYC |
| a chemical moiety read as the enzyme | `Ribosylation  ADP → histone H1` | "ADP ribosylation of histone H1 promotes transcription…" — "ADP-ribosylation" got split, and `ADP` grounded to `CHEBI:CHEBI:16761` |
| a citation marker read as an entity | `Complex NF-kappaB → PARP1`, `detail=members=NFkappaB\|PARP1\|A43` | the third "member" `A43` is the inline reference label `[[A43]]` |
| long-range attachment | `Inhibition  SIRT1 → transcription` | "p53 acts as a transcription factor that induces apoptosis and is inhibited by SIRT1 deacetylation" — the inhibition target is p53, not transcription |

None of these are grounding bugs; they are parse bugs. Rule-based readers are
brittle-but-precise on clean canonical sentences and fall apart on long ones. This
is the baseline a newer extractor would be compared against.

### Stage 4 — Grounding and sequence mapping

**In:** raw Statements. **Out:** the same Statements with cleaned `db_refs`.

```python
stmts = ac.map_grounding(stmts)   # indra_eval/pipeline.py:132
stmts = ac.map_sequence(stmts)    # skipped on failure, with a note
```

Grounding is the CURIE assignment: an `Agent` carries a `db_refs` dict, and
`map_grounding` applies INDRA's curated grounding map to correct and normalize it.
The namespaces you will actually see in this run:

| namespace | meaning | example from the run |
|---|---|---|
| `HGNC` | human gene symbol authority | `SIRT1` → `HGNC:14929` |
| `UP` | UniProt protein accession | `SIRT1` → `UP:Q96EB6` |
| `FPLX` | FamPlex — protein families and named complexes, for things with no single gene | `AMPK` → `FPLX:AMPK`, `NF-kappaB` → `FPLX:NFkappaB` |
| `CHEBI` | small molecules | `ADP` → `CHEBI:CHEBI:16761` |
| `MESH` | MeSH — used here mostly for *biological processes* | `apoptosis` → `MESH:D017209`, `transcription` → `MESH:D014158` |
| `EGID` | Entrez gene id, carried alongside | `SIRT1` → `EGID:23411` |
| `TEXT` | not a grounding — the surface string the reader saw | `TEXT: "SIRT1"` |

Note that `db_refs` holds *several at once*. `findings.tsv` picks one for display,
by the priority list in `indra_eval/outputs.py:26`: HGNC, UP, UPPRO, FPLX, CHEBI,
PUBCHEM, MESH, GO, … with `TEXT` explicitly excluded. So a TSV grounding of
`HGNC:14929` does not mean UniProt was absent — look in `statements.json`.

Namespace distribution across the 71 assembled statements in this run:

| position | HGNC | FPLX | MESH | CHEBI | ungrounded |
|---|---|---|---|---|---|
| subject | 50 | 15 | 1 | 2 | 3 |
| object | 50 | 4 | 12 | 2 | 3 |

Read that carefully: only **35 of 71** statements have *both* agents at
HGNC/UniProt gene level. The rest point at a family (`FPLX:AMPK`), a process
(`MESH:D014158` transcription), a metabolite, or nothing. This matters enormously
in §5 — a triple whose object is "transcription" has nothing to match in SIGNOR.

**What can go wrong:** wrong-but-confident groundings. `runs/PMC3898398/findings.tsv`
has `Complex  CRY → BMAL1` with the subject grounded to `HGNC:18246` — that is
**CRYL1**, a lambda-crystallin-like gene, not the circadian cryptochromes CRY1/CRY2
the sentence meant. Three statements have a genuinely ungrounded agent
(`H4K16`, `H4K20me2`, `NAD+`), where the grounding map has no entry for a histone
mark or where the reader's surface form is too odd.

`summary.md` reports the aggregate as **grounding coverage: 87.3%** — the share of
statements where *every* agent got some non-TEXT grounding. It says nothing about
whether the grounding is *right*.

**See it yourself:**
```bash
cut -f1,2,3,4,5 runs/PMC3898398/findings.tsv | column -t -s$'\t' | head -20
```

### Stage 5 — Preassembly and belief

**In:** grounded Statements. **Out:** 71 deduplicated Statements with belief scores.

```python
ac.run_preassembly(stmts, return_toplevel=True)
```

Preassembly is INDRA's dedup-and-hierarchy step, and it is the single most
valuable thing INDRA does that a naive extraction pipeline does not.

1. Every Statement gets a **matches key** and its hash. Two statements with the
   same type, the same agents-in-the-same-roles, and the same modification details
   are *the same statement*.
2. Duplicates are collapsed into one Statement whose `evidence` list is the union
   of theirs.
3. A refinement hierarchy is computed (a statement about `MAPK1` refines one about
   the `MAPK` family), and `return_toplevel=True` returns only the most specific
   statements, with the general ones reachable via `supports`/`supported_by`.
4. **Belief** is recomputed from the merged evidence.

The numbers on our run: **90 raw → 71 assembled**. Of the 71, 62 have one evidence
and 9 have two. And belief tracks exactly that:

| belief | count | evidences |
|---|---|---|
| 0.65 | 62 | 1 |
| 0.86 | 9 | 2 |

Those two values are not coincidences. INDRA's default belief priors
(`indra/resources/default_belief_probs.json`) give REACH a **systematic** error
rate of 0.05 and a **random** error rate of 0.3, and the scorer computes

```
belief = 1 − (syst + rand^n)      # n = number of independent evidences
n=1:  1 − (0.05 + 0.3 )  = 0.65
n=2:  1 − (0.05 + 0.09)  = 0.86
```

The systematic term is the floor: no amount of evidence from REACH alone can push
belief above 0.95, because the model assumes REACH is systematically wrong 5% of
the time. Getting past that needs a *second source*. **Belief is a ranking signal,
not a calibrated probability** — do not read 0.86 as "86% likely true".

Here is the dedup happening, from `runs/PMC3898398/statements.json` — one
`Complex(CLOCK, BMAL1)` statement built out of two different sentences:

```json
{
  "type": "Complex",
  "belief": 0.86,
  "matches_hash": "12643829116021084",
  "members": [
    {"name": "CLOCK", "db_refs": {"EGID": "9575", "HGNC": "2082", "TEXT": "CLOCK", "UP": "O15516"}},
    {"name": "BMAL1", "db_refs": {"EGID": "406",  "HGNC": "701",  "TEXT": "BMAL1", "UP": "O00327"}}
  ],
  "evidence": [
    {"source_api": "reach", "pmid": "24360018",
     "text": "The core circadian machinery involves a transactivating CLOCK and BMAL1 heterodimer, ...",
     "annotations": {"found_by": "binding_complex_A_B", "agents": {"coords": [[56, 61], [62, 67]]}},
     "epistemics": {"direct": true}},
    {"source_api": "reach", "pmid": "24360018",
     "text": "PARP1 is capable of ADP ribosylating CLOCK in a circadian manner disrupting the association between the BMAL1 and CLOCK heterodimer and its targets [[A66]] [XREF_BIBR].",
     "annotations": {"found_by": "binding_complex_A_B", "agents": {"coords": [[104, 109], [110, 115]]}},
     "epistemics": {"direct": true}}
  ]
}
```

Two sentences, one statement, belief bumped from 0.65 to 0.86, both quotes kept.
Note the second sentence is arguably about the complex being *disrupted* — see the
negation gap in §6.

**What can go wrong:** over-merging when grounding is wrong (two different proteins
both grounded to `HGNC:270` merge into one statement — the run has both
`DecreaseAmount PARP → SIRT1` at `HGNC:270` and `DecreaseAmount PARP2 → SIRT1` at
`HGNC:272` from the same sentence, so here it *didn't* merge, but the mechanism is
obvious). And under-merging when two sentences say the same thing with different
specificity that the hierarchy doesn't relate.

### Stage 6 — The four outputs

Written by `indra_eval/outputs.py:202`, all four, always.

**`statements.json`** — `stmts_to_json(stmts)`, a JSON array of statement objects.
This is the **full-fidelity record**. Everything else is a lossy view of it. If you
are deciding whether INDRA's model carries something you need, look here.

**`findings.tsv`** — one row per statement, ten columns, for eyeballing and
diffing. Real header plus one real row:

```
type	subject_text	subject_grounding	object_text	object_grounding	detail	belief	evidence_sentence	pmid	source_api
Phosphorylation	AMPK	FPLX:AMPK	SIRT1	HGNC:14929	residue=T;position=344	0.6500	AMPK phosphorylates human SIRT1 at T344 inhibiting its ability to decacetylate p53, a known target of SIRT1 [[A8]] [XREF_BIBR].	24360018	reach
```

What the TSV throws away, and you should know it does:
- **only the first two agents.** `Complex NF-kappaB → PARP1` in this run actually
  has three members; the third only shows up inside `detail=members=…`.
- **only the first evidence.** The `Complex(CLOCK, BMAL1)` row shows one of its two
  sentences; belief 0.86 is your only hint that there is another.
- **only one grounding per agent**, per the priority list.
- **no `epistemics`** — no `direct`, no `negated`, no `hypothesis`.

**`report.html`** — `indra.assemblers.html.HtmlAssembler` output (629 KB for this
run). Statements grouped and collapsible, each with its supporting sentences and
links out to the source article. This is the fastest way for a human to judge
whether a batch of extractions is any good. If the assembler throws,
`outputs.write_report_html` writes a stub page naming the exception rather than
failing the run.

**`summary.md`** — the run's vital signs: reader, input kind, raw vs assembled
counts, grounding coverage, wall-clock, any pipeline notes, counts by statement
type, and the top-10 entities. For PMC3898398 the type histogram is Inhibition 14,
Activation 13, Complex 12, Phosphorylation 12, DecreaseAmount 4, IncreaseAmount 4,
Acetylation 3, Deacetylation 3, Ribosylation 3, Sumoylation 2, Methylation 1; the
top entities are SIRT1 (29 mentions) and PARP1 (21) — which is exactly right for a
paper titled *SIRT1/PARP1 crosstalk*.

---

## 3. How to read a Statement

A `Statement` is four things: a **type**, some **agents in named roles**, some
**type-specific detail**, and a list of **evidence**.

### The type is the predicate

INDRA has ~40 statement types and they *are* the relation vocabulary:
`Phosphorylation`, `Dephosphorylation`, `Acetylation`, `Deacetylation`,
`Methylation`, `Sumoylation`, `Ribosylation`, `Activation`, `Inhibition`,
`Complex`, `IncreaseAmount`, `DecreaseAmount`, `Translocation`, … Eleven of them
appear in this one paper. The Python class name *is* the type string in JSON.

### Agents live in role-named slots, not in `subject`/`object`

This trips everyone up. The slot names depend on the type:

| type family | slots |
|---|---|
| modifications (`Phosphorylation`, `Acetylation`, …) | `enz` (enzyme), `sub` (substrate) |
| regulations (`Activation`, `Inhibition`, `IncreaseAmount`, `DecreaseAmount`) | `subj`, `obj` |
| `Complex` | `members` — a *list*, order not meaningful, can be longer than 2 |
| `Translocation` | `agent`, plus `from_location` / `to_location` |

`stmt.agent_list()` gives you them positionally regardless of type, which is what
`outputs.py` uses to fill `subject_*` and `object_*`.

### `db_refs` is a dict of groundings, not one CURIE

```json
"sub": {"name": "SIRT1",
        "db_refs": {"EGID": "23411", "HGNC": "14929", "TEXT": "SIRT1", "UP": "Q96EB6"}}
```

`name` is INDRA's canonical label. `TEXT` is the reader's surface string and is
*not* a grounding. Everything else is a real namespace, and there can be several.
There is no single canonical CURIE per agent — you pick one, which is exactly what
the priority list in `outputs.py` does and exactly what the benchmark has to do
again in §5.

### Type-specific detail is in named fields, not qualifiers

The real record for the row we quoted above, from `runs/PMC3898398/statements.json`:

```json
{
  "type": "Phosphorylation",
  "belief": 0.65,
  "matches_hash": "4756454066562644",
  "residue": "T",
  "position": "344",
  "enz": {"name": "AMPK",  "db_refs": {"ECCODE": "2.7.11.31", "FPLX": "AMPK", "NCIT": "C94701", "TEXT": "AMPK"}},
  "sub": {"name": "SIRT1", "db_refs": {"EGID": "23411", "HGNC": "14929", "TEXT": "SIRT1", "UP": "Q96EB6"}},
  "evidence": [{
    "source_api": "reach",
    "pmid": "24360018",
    "text": "AMPK phosphorylates human SIRT1 at T344 inhibiting its ability to decacetylate p53, a known target of SIRT1 [[A8]] [XREF_BIBR].",
    "text_refs": {"PMID": "24360018"},
    "epistemics": {"direct": true, "raw_sections": []},
    "annotations": {
      "found_by": "Phosphorylation_syntax_1a_verb",
      "agents": {"raw_text": ["AMPK", "SIRT1"], "coords": [[0, 4], [26, 31]],
                 "raw_grounding": [{"FPLX": "AMPK", "ECCODE": "2.7.11.31", "NCIT": "C94701", "TEXT": "AMPK"},
                                   {"HGNC": "14929", "EGID": "23411", "UP": "Q96EB6", "TEXT": "SIRT1"}]}
    }
  }]
}
```

Trace it: `residue=T` + `position=344` is the `detail` column in the TSV.
`FPLX:AMPK` is the `subject_grounding` — a *family*, because "AMPK" is a
heterotrimer with no single gene. `annotations.raw_grounding` is the grounding
*before* `map_grounding` touched it, so you can diff pre- and post-mapping.
`coords` are character offsets into `evidence.text` — the only positional
provenance you get, and it is into the sentence, not into the document.

### Evidence is a sentence plus a source

Each `Evidence` has `source_api`, `pmid`, `text`, `text_refs`, `annotations`, and
`epistemics`. `epistemics` may carry `direct`, `negated`, `hypothesis` — per
evidence, never on the statement (see §6).

### Belief is on the statement

One float, recomputed by preassembly from the whole evidence list. See stage 5.

---

## 4. The other half of INDRA: reading databases

`indra_eval/signor_demo.py` shows the same `Statement` objects arriving from a
curated database instead of a reader. `signor.process_from_web()` downloads
SIGNOR's TSVs and converts each row into a Statement — high belief, and a database
row rather than a sentence as evidence.

```bash
uv run python -m indra_eval.signor_demo
uv run python -m indra_eval.signor_demo signor_all_data.tsv signor_complexes.tsv
```

**As of 2026-08-20 `signor.uniroma2.it` does not respond at all**, so the live
path is a 60s connection timeout, a message naming the failure, and exit code 1.
The offline form takes hand-downloaded TSVs. This is why the benchmark uses the
already-landed dumps in `signor_landing_data/` instead of the live service — treat
academic web services as down-by-default.

---

## 5. How the benchmark judges a reader

> **Status: final.** The loop is closed. Every number in this section was read out of
> `benchmark/reach/scores.json` and `benchmark/reach-modern/scores.json`, produced by
> `indra_eval/signor_gt.py` + `indra_eval/score.py` over the committed 50-paper
> corpus in `benchmark/corpus.json`. The per-paper detail is in
> `benchmark/<reader>/{per_paper.tsv, report.md}`.

### The question the benchmark answers

There are two REACH variants: **v14** (`pathwaycommons/reach-docker:latest`,
2018-era, amd64 under Rosetta, port 8080) and **modern** (`reach-modern:latest`,
built from clulab/reach master, port 8081). The benchmark exists to decide, with
numbers rather than vibes, **which one should be the default reader**. It did — see
"The answer" below.

### The ground truth

SIGNOR is a manually curated signaling-interaction database. Each row is one
curator asserting one causal relation, *with the PMID they read it in*. That last
part is what makes it usable: for a given paper we know what a human expert
extracted from it, so we can ask what the reader missed.

`signor_landing_data/signor_human.tsv` is headerless with 28 positional columns
(`ENTITY_A, TYPE_A, ID_A, DATABASE_A, ENTITY_B, …, PMID, DIRECT, NOTES, ANNOTATOR,
SENTENCE, SIGNOR_ID, SCORE`). Running the loader:

```bash
uv run python -m indra_eval.signor_gt
```

```json
{
  "rows": 42476,
  "excluded_unknown_effect": 1083,
  "excluded_no_pmid": 730,
  "kept": 40663,
  "buckets": {
    "('activity', 'increased')": 11441,
    "(None, 'increased')": 7164,
    "('activity', 'decreased')": 5799,
    "('abundance', 'increased')": 5451,
    "('abundance', 'decreased')": 4447,
    "(None, 'decreased')": 3503,
    "in_complex_with": 2858
  },
  "distinct_pmids": 13891
}
```

Scoring is **human only** (mouse and rat dumps load but aren't scored). 40,663
usable rows across 13,891 PMIDs is the pool the corpus is sampled from.

### The core match key

Everything hinges on reducing both sides to the same tuple:

```
(subject_id, object_id, aspect, direction)      matched within one PMID
```

- **`aspect`** — *what* about the object changed: `activity` or `abundance`.
- **`direction`** — *which way*: `increased` or `decreased`.

SIGNOR's `EFFECT` column maps in (`signor_gt.map_effect`):

| EFFECT | aspect | direction |
|---|---|---|
| `up-regulates activity` | activity | increased |
| `down-regulates activity` | activity | decreased |
| `up-regulates quantity…` | abundance | increased |
| `down-regulates quantity…` | abundance | decreased |
| bare `up-regulates` | **None** (wildcard) | increased |
| bare `down-regulates` | **None** (wildcard) | decreased |
| `form complex` | — | — (predicate `in_complex_with`) |
| `unknown` | *row excluded from scoring* | |

INDRA statement types map out (per `docs/SCORING.md`):

| statement type | aspect | direction |
|---|---|---|
| `Activation` | activity | increased |
| `Inhibition` | activity | decreased |
| `IncreaseAmount` | abundance | increased |
| `DecreaseAmount` | abundance | decreased |
| `Phosphorylation` and other modifications | that PTM | increased |
| `Dephosphorylation` and other de-modifications | that PTM | decreased |
| `Complex` | `in_complex_with`, matched unordered on `(subject_id, object_id)` |

A bare `up-regulates` row has `aspect=None` and matches *any* candidate aspect —
the curator didn't distinguish, so neither do we.

### Two scoring adjustments

Hand-tracing false negatives on the first 50-paper v14 run turned up two places
where the naive key scored a *correct* extraction as a miss. Both were amended into
the scoring spec (`docs/SCORING.md`) and both apply identically to every reader, so
comparisons stay internally consistent.

**1. SIGNOR-native entities are out of the CURIE-matching game.** A ground-truth row
whose `subject_id` or `object_id` starts with `SIGNOR` is not a protein with a
UniProt accession — it is a SIGNOR-internal complex, protein family, phenotype, or
stimulus. Nothing INDRA emits can ever match one, so counting them as misses
measures the id systems, not the reader. Those rows are excluded from the recall
denominator and reported as `n_gt_unmatchable`. On this corpus that is **62 of 144
rows** — 43% of the ground truth is unreachable by construction. Headline recall is
therefore `TP / matchable rows`, with all-rows recall kept as a secondary line.

**2. Mechanism-aware matching for Modifications.** SIGNOR curates "PP1α
dephosphorylates KAP1" as `EFFECT=(activity, increased)` plus
`MECHANISM=dephosphorylation`, while the correct extraction is
`Dephosphorylation(PP1, KAP1)` — the same claim, keyed differently. So a candidate
Modification tuple also matches a GT row on the same `(subject_id, object_id)` when
SIGNOR's `MECHANISM` maps to the same PTM family with consistent direction, via a
small curated map: phosphorylation↔(phosphorylation, increased),
dephosphorylation↔(phosphorylation, decreased),
(poly/mono)ubiquitination↔(ubiquitination, increased),
deubiquitination↔(ubiquitination, decreased), and acetylation/deacetylation and
methylation/demethylation likewise. Mechanism-level hits merge into headline TP and
are *also* reported separately as `tp_mechanism`, so the lift is always visible: for
v1.4 it is **8 of 31 true positives**.

### Entity ids: the hard part

SIGNOR keys on **UniProt**. INDRA gives you whatever `db_refs` it has. So:
prefer `db_refs['UP']`; else map `HGNC → UniProt` via
`indra.databases.hgnc_client.get_uniprot_id`; and if the agent is `FPLX`
(a family), `MESH` (a process), or ungrounded, **it is unmatchable**.

That is not a rounding error. In `runs/PMC3898398/findings.tsv`, only **35 of 71**
statements have both agents at HGNC/UniProt gene level. Fifteen subjects are
`FPLX` families (`FPLX:AMPK`, `FPLX:CK2`, `FPLX:Histone_H1`) and twelve objects
are `MESH` processes (`MESH:D014158` transcription, `MESH:D017209` apoptosis).
A statement like `Phosphorylation AMPK → SIRT1 T344` is *biologically correct and
mechanistically specific* and still cannot be matched, because SIGNOR wants
PRKAA1/PRKAA2, not the AMPK family.

Because that choice moves the number so much, the scoring spec requires reporting
**both**: *strict* precision (unmatchable extractions count against you) and
*grounded-only* precision (they are excluded from the denominator).

### What P/R/F1 mean *here*

- **Precision** = of the statements the reader produced for this paper that the
  match key can represent, what fraction hit a SIGNOR row for the same PMID.
- **Recall** = of that paper's usable human SIGNOR rows, what fraction the reader
  found. The denominator is *SIGNOR's* rows, so recall is "how much of what a
  curator wrote down did the machine also get".
- **F1** = their harmonic mean, reported per-paper and aggregated, plus slices by
  SIGNOR `MECHANISM` and by INDRA statement type.

### The answer

Corpus: 50 papers sampled with `--seed 42` from human SIGNOR PMIDs that have PMC OA
full text. Both readers ran every paper; both scored against the same 144 GT rows,
82 of them matchable.

| | REACH v1.4 | REACH modern |
|---|---|---|
| recall (matchable rows) | **0.378** (31/82) | 0.342 (28/82) |
| recall (all 144 rows) | 0.215 | 0.194 |
| precision, strict | 0.029 | 0.023 |
| precision, grounded-only | 0.071 | 0.057 |
| F1 (strict) | **0.054** | 0.043 |
| true positives | 31 = 23 core + 8 mechanism | 28 = 23 core + 5 mechanism |
| false positives (strict) | 1039 | 1206 |
| false negatives | 51 | 54 |
| candidate statements | 1540 | 1855 |
| papers failed | 0 | 0 |
| wall-clock per paper | ~45s | ~2× slower |

**The 3-true-positive gap at n=50 is within noise.** Nobody should read
`0.378 vs 0.342` as "v1.4 reads biology better than a reader seven years newer".
Twenty-three of the core true positives are *the same 23* for both readers. Modern
found more candidates (1855 vs 1540) and converted fewer of them.

**The decisive margin was operational:**

- Modern OOM'd — docker exit 137 — on 4 of the 50 papers and only completed after
  raising `SBT_OPTS=-Xmx10g`.
- Its DyNet native library is **x86_64-only**; there is no arm64 build, so the
  "modern, native" variant also ends up running under Rosetta. The main reason to
  want it evaporates.
- It is roughly **2× slower per paper** (528.1s of scoring-side wall-clock over the
  corpus versus 89.1s, on top of the slower reads themselves).

So: **the default reader stays `reach` (v1.4).** Modern remains installable and
supported — `indra-eval reach start --modern`, then
`indra-eval read <id> --reader reach-modern` — it is simply not the default.

### Why precision is ~0.03, and why that is not the reader being wrong

Precision here is **structurally deflated**, and understanding why matters more than
the number. v1.4 produced 1540 candidate tuples across 50 papers and was scored
against 82 matchable ground-truth rows. Even a *perfect* reader would post terrible
precision on that arithmetic, because it would correctly extract far more true
biology from a full paper than SIGNOR chose to curate from it. This is a **coverage
mismatch, not wrongness** — exactly the mechanism described in "Entity ids" above,
now with real numbers:

- Of 1540 candidates, only **435 were grounded** well enough to carry a UniProt id
  at all; 1083 agent resolutions came back `ungrounded` and 9 statements had an
  unmappable type. That is why strict (0.029) and grounded-only (0.071) precision
  differ by more than 2×.
- A curator reads a paper for the one or two interactions it is *about*. REACH reads
  every sentence, including the review-article background. `Activation` alone
  contributed 334 candidates and 9 matches.

The one number that *is* diagnostic is the by-mechanism recall slice, because it has
a denominator SIGNOR actually defined. For v1.4: `binding` 4/5 = 0.80,
`dephosphorylation` 4/6 = 0.67, `ubiquitination` 2/3 = 0.67, `phosphorylation`
20/35 = 0.57, `transcriptional regulation` 1/10 = 0.10, and
`post transcriptional regulation`, `relocalization`, `chemical inhibition` all 0.
That is the shape you should expect from a rule-based reader built for PTM
signaling: it finds the phosphorylation grammar it was written for and misses
everything expressed at a distance.

### The honest caveats

**SIGNOR is a signaling-subfield sample.** It is curated post-translational
signaling: kinases, phosphatases, complexes. It is not a sample of the biomedical
literature. A reader tuned to phosphorylation will look better on SIGNOR than it
is; results do **not** generalize. The scoring spec requires every report to say so, and
`scores.json` carries the sentence in a `caveat` field so it travels with the
numbers:

> SIGNOR is a curated signaling-subfield sample (mostly human protein signaling,
> hand-picked sentences). These numbers describe agreement with that slice of the
> literature only and do NOT generalize to the full literature or to non-signaling
> claims.

**n=50 is small.** 82 matchable rows and 31 true positives means a single paper can
move recall by a point or two. It is enough to pick a default reader on operational
grounds; it is not enough to rank two readers on accuracy.

**"Not in SIGNOR" ≠ "wrong".** A false positive here often means the curator
didn't record it, considered it background, or it fell outside SIGNOR's scope. The
`Activation SIRT1 → cell cycle` row in our run is a defensible reading of its
sentence and has no possible SIGNOR counterpart.

**Negation is untestable.** SIGNOR rows are assertions. INDRA has no
statement-level polarity. So a reader that turns "X does not phosphorylate Y" into
`Phosphorylation(X, Y)` is scored exactly like one that got it right — and might
even be *rewarded* if SIGNOR happens to have a positive row for that pair from
another sentence. The benchmark cannot see this class of error at all.

**The demo paper is not a benchmark paper.** PMID 24360018 appears **zero** times
in `signor_landing_data/signor_human.tsv`, which is why every example in this
document is illustrative of the *pipeline* and not of a *score*. The corpus is
sampled the other way round: start from human SIGNOR PMIDs, keep the ones with PMC
OA full text (`indra-eval corpus build --n 50 --seed 42`).

**Only the first evidence is compared per statement**, and only within one PMID —
a statement supported by two papers is scored once against each.

---

## 6. What INDRA is *not* giving us

The gaps, stated the way you'd want them stated before you build on this.

**The readers predate the LLM era.** REACH's core is a rule and grammar system
over dependency parses — the `found_by` strings in the evidence annotations
(`Phosphorylation_syntax_1a_verb`, `binding_complex_A_B`) are literally rule names.
TRIPS/DRUM is a hand-built semantic parser. Neither has a language model in it.
The consequence is a specific failure shape: clean canonical sentences extract
well; hedged, coordinated, long-range, or cross-sentence claims are simply not
seen. **Recall is the weak axis, not precision** — and even precision fails in the
specific ways catalogued in stage 3, where the parse attaches the wrong argument.

**No Biolink, and no mapping to it.** Statement types are INDRA's own closed
vocabulary. There is no `biolink:` predicate anywhere in the box, no qualifier
grammar, no crosswalk. To produce Biolink-shaped output, the
INDRA→Biolink predicate mapping has to be written separately — and it is not a clean 1:1, because
`Activation`/`Inhibition` conflate "activity" and "regulation" in ways Biolink
splits with qualifiers.

**No general qualifier slot.** INDRA carries *specific* extras in *specific*
fields: `residue`/`position` on modifications, `obj_activity` on regulations,
`Agent.mods` / `Agent.mutations` / `Agent.bound_conditions` for protein state,
`from_location`/`to_location` on translocation. That covers a lot of molecular
biology precisely, and covers everything else not at all: no cell type, no tissue,
no species qualifier, no dose, no experimental context. SIGNOR's own
`CELL_DATA`/`TISSUE_DATA` columns have nowhere to go on the INDRA side.

**No statement-level negation or hypothesis flag.** `Evidence.epistemics` may hold
`negated` / `hypothesis` / `direct` for one evidence — the `Complex(CLOCK, BMAL1)`
evidence above has `"direct": true`. But the `Statement` itself has no polarity
field, so a negated mention and an asserted one produce the *same statement type*,
and preassembly will happily merge them into one statement with two evidences.
Look at the second `Complex(CLOCK, BMAL1)` sentence: "…disrupting the association
between the BMAL1 and CLOCK heterodimer…" is contributing evidence *for* the
complex. If polarity matters to you, you must read `epistemics` yourself, per
evidence, and decide what to do when they disagree.

**Groundings are per-statement, not canonicalized per-entity.** One entity can be
`HGNC` in one statement and `UP` in another; `map_grounding` fixes known errors but
does not collapse everything to one preferred prefix. Any downstream CURIE
normalization is on you. And a wrong grounding is silent — nothing in the output
flags `CRY → HGNC:18246 (CRYL1)` as suspicious.

**Belief is not a calibrated probability.** It is a function of source reliability
and evidence count. In this run it takes exactly two values, 0.65 and 0.86, because
every statement has exactly one or two REACH evidences. Use it to rank, never as
`P(true)`.

**No document structure.** `Evidence` is a sentence plus a PMID. There is no
section, no paragraph, no character offset into the source document (the `coords`
are offsets into the sentence), no figure or table provenance. You cannot tell
whether a statement came from the abstract, the results, or the discussion of prior
work — and in a review article like PMC3898398, essentially everything is a claim
about someone else's paper. Note how many evidence sentences contain `XREF_BIBR`:
that is a citation the reader stripped, and the claim belongs to the cited work,
not to this one. INDRA will happily stamp it with PMID 24360018.

**No confidence about what it didn't find.** There is no "considered and rejected"
signal. A missing statement and an absent claim are indistinguishable.

---

## 7. Run it yourself

```bash
# once
uv sync                                    # python 3.12, indra 1.24.0

# bring up the reader (first boot: 5-15 min under emulation — not hung)
uv run indra-eval reach start
uv run indra-eval reach status              # -> "REACH is READY at http://localhost:8080/api/text"

# read things
uv run indra-eval read PMC3898398           # full text, local REACH  (~60s once warm)
uv run indra-eval read 32133153 --reader trips              # abstract via TRIPS
uv run indra-eval read "MEK phosphorylates ERK." --reader trips

# look at what came out
cat runs/PMC3898398/summary.md
column -t -s$'\t' runs/PMC3898398/findings.tsv | less -S
open runs/PMC3898398/report.html

# the ground truth (no network needed — dumps are landed in signor_landing_data/)
uv run python -m indra_eval.signor_gt

# tear down
uv run indra-eval reach stop
```

The modern variant and the benchmark:

```bash
uv run indra-eval reach start --modern                      # port 8081; needs SBT_OPTS=-Xmx10g
uv run indra-eval read PMC3898398 --reader reach-modern
uv run indra-eval corpus build --n 50 --seed 42             # -> benchmark/corpus.json (committed)
uv run indra-eval benchmark --reader reach                  # -> benchmark/reach/{scores.json,per_paper.tsv,report.md}
uv run indra-eval benchmark --reader reach-modern
```

The benchmark caches: a paper whose run directory already holds `statements.json`
for that reader is scored, not re-read. Re-running it over the committed corpus
reproduces the numbers in §5 without touching the readers.

### Troubleshooting, shortest form

| symptom | cause | fix |
|---|---|---|
| first read hangs for minutes | JVM boot + lazy NLP model load | wait; 5–15 min is normal |
| container exits immediately, no message | Java 8 SIGSEGV at the vsyscall page under qemu | enable `UseVirtualizationFrameworkRosetta` in Docker's `settings-store.json`, restart Docker |
| 404 from the REACH container | text sent as a body instead of a query parameter | `POST /api/text?text=…&output=fries` with an empty body |
| REACH stopped answering | JVM OOM on a large document | `reach stop && reach start`; raise Docker Desktop's memory |
| modern container dies with exit 137 | JVM OOM — hit 4 of the 50 benchmark papers | run it with `SBT_OPTS=-Xmx10g` |
| modern is slower than v1.4 despite being "native" | its DyNet native lib is x86_64-only, so it runs under Rosetta too | expected; ~2× slower per paper |
| `Could not import jnius` warning | INDRA's offline JVM reading path is unavailable | harmless — we use the HTTP service |
| SIGNOR demo times out | `signor.uniroma2.it` is down | use the landed dumps / pass TSV paths |

---

## Where to look in the code

| concern | file |
|---|---|
| stage orchestration | `indra_eval/pipeline.py` |
| argument resolution, PMC/PubMed fetch, PMID extraction | `indra_eval/fetch.py` |
| docker lifecycle, readiness probe, both REACH variants | `indra_eval/reach_service.py` |
| the four outputs, grounding priority list | `indra_eval/outputs.py` |
| CLI surface | `indra_eval/cli.py` |
| SIGNOR ground truth normalization | `indra_eval/signor_gt.py` |
| database-reading demo | `indra_eval/signor_demo.py` |
| benchmark ground truth and scoring rules | `docs/SCORING.md` |
