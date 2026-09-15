# Starts at Attention

A research atlas from **Attention Is All You Need** to modern LLM architectures,
reasoning, vision, robotics, GPU systems, and benchmarks. One HTML page, one Python
updater, no package dependencies.

Public graph: **[Starts at Attention](https://harshtomarcode.github.io/starts-at-attention/)**.

The entire page is the graph. Hover to inspect a paper, click to trace its lineage,
double-click to read the original, drag a bubble to stretch its connections, drag
the background to pan, and scroll through time. Each screen spans about seven months,
with Text/LLMs centered and milestone captions on the right. Hold Control/Command
while scrolling to zoom. Double-click empty space or press Home to return to
Attention Is All You Need. Arrow keys move between papers; Enter opens the selected source.
Reduced-motion settings disable the ambient wobble. Category names across the top
are hidden; bubble colors still identify the lanes.

The tracked catalog grows independently of the visible graph. Selection uses a
650-paper guide with room to grow and shrink, normally around 600–700 papers.
Current counts, latest tracked and visible publication dates, admissions, removals,
and recent exclusion reasons appear in `data/papers.json` under `pipelineStatus`
and in each GitHub updater run's summary. Unknown measurements remain unknown.

## Run locally

```sh
python3 update.py --build
python3 -m http.server 4173 --bind 127.0.0.1
```

Open <http://127.0.0.1:4173/>. The deployable copy is `dist/`.

## Publish publicly

GitHub Pages serves the static graph without a sign-in. The `Publish research atlas`
workflow builds `dist/` and deploys it after site or data changes on `main`, on manual
dispatch, and after a successful `Update research atlas` run. The completion trigger
also publishes automated data commits, which do not themselves trigger push workflows.
Pages uses GitHub Actions as its publishing source. Only the HTML, graph export, and
abstract detail files are included in the deployed site.

## Update daily

```sh
python3 update.py --discover --refresh --limit 3000 --build
```

The GitHub Actions workflow runs daily at **12:23 UTC**, on manual dispatch, and
when implementation changes are pushed to `main`. It commits successful catalog
updates to this repository. Hosted copies read the latest GitHub graph export
on page load, falling back to their bundled snapshot if GitHub is unavailable.
The browser loads abstracts by year only when a paper is opened.

GitHub's standard hosted runners are currently
[free for public repositories](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
The workflow uses no paid model calls. Scheduled runs may be delayed; GitHub may
[disable inactive schedules after 60 days](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).

arXiv supplies broad discovery, first-submission dates, authors, and abstracts.
[Hugging Face Daily Papers](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api#huggingface_hub.HfApi.list_daily_papers)
adds a rolling seven-day community shortlist. The feeds deduplicate by arXiv ID and
fail independently: a throttled or incomplete source records a warning and a resume
cursor while successful pages and other providers can still be saved. Validation
failures stop the update without replacing the saved collection.

Hugging Face feature dates and upvotes are separate metadata. Featured papers get
earlier evidence lookup; votes remain descriptive and never become citations or score. HF organizations and
submitters are not treated as publication affiliations. New HF-only dates are
labeled provisional until arXiv verifies them. Existing canonical metadata is retained.

Semantic Scholar supplies indexed citation counts, references, and author profiles.
Its shared unauthenticated API can throttle requests; an optional
`SEMANTIC_SCHOLAR_API_KEY` repository secret or local environment variable improves
access. Provider failures preserve saved evidence and valid batches. New papers
can acquire author identities directly through their arXiv ID in the same run;
empty author responses remain missing and are retried daily, while valid profiles
refresh weekly. Two batches of recent papers missing lineage alternate with one
historical refresh batch, and HF-featured papers are prioritized within each rotation.
Daily work is bounded to 500 paper-author lookups, 5,000 author profiles, and 1,000
citation/reference lookups so the backlog rotates without exhausting the job's
30-minute runtime. Scoring still covers the entire retained catalog on every run.
The daily discovery cursor resumes capped windows, with overlapping dates to catch
indexing delays. Coverage is arXiv-focused, not an exhaustive census of AI research.

## Data and ranking

- `data/catalog.json`: canonical metadata, abstracts, source links, references,
  selection status, score evidence, and policy.
- `data/papers.json`: graph export; no abstracts.
- `data/details/YYYY.json`: abstracts and detail provenance, loaded on demand.
- `data/history.jsonl`: score and membership changes.
- `PLAN.md`: product decisions and learning goals.

No PDFs are stored. Abstract text belongs to the original authors; each record
links to its source. Third-party papers and abstracts retain their original rights.
Indexed counts can differ across paper versions and are timestamped.
Unknown citation counts are never replaced with invented numbers.

Solid arrows run from an earlier paper to a later paper that cites it, verified
against the citation index. Dashed arrows are explicitly curated learning
prerequisites. Bubble radius follows the square root of the consequentiality score,
from 1.4 to 18 CSS pixels at default zoom. Hovering shows the age-dependent weights and the weighted citation and
author/company contributions. Missing citation data appears as a hollow bubble.

Policy version 5 preserves the linear transition between two signals on a 0–100 scale:

```
citationWeight = min(1, age_in_months / 24)
reputationWeight = 1 - citationWeight
citationSignal = min(100, citations / 1000)
reputationSignal = 100 * max(company, curatedAuthor, indexedAuthor)
score = citationWeight * citationSignal + reputationWeight * reputationSignal
```

At release the weights are 0% citations / 100% reputation; after six months they
are 25% / 75%; after a year 50% / 50%; and after two years 100% / 0%. Recent papers
therefore do not need their own citation history to gain prominence. Missing
signals remain unknown, contribute no points, and do not transfer their unused
weight to the other signal. Scores with no usable evidence remain pending.

A company signal requires a sourced publication affiliation or an exact corporate
author. Curated author weights remain explicit in the policy. The indexed author
signal is the strongest publication-linked author's `min(1, hIndex / 100)`.
The maximum avoids rewarding a paper merely for having a large author list.
Names must match the paper's arXiv author list after case and punctuation
normalization; profiles are retrieved through publication-linked Semantic Scholar
IDs, never a free-text author search. Initials and other unmatched aliases are
conservatively excluded. These are reputation estimates, not proof of a new
paper's quality, and the citation index may still have identity errors.

Author IDs are refreshed with paper metadata. Author metrics are refreshed weekly,
while scores and admission thresholds are recalculated daily. Partial provider
failures preserve earlier evidence. The canonical catalog stores author profiles
once by ID; the browser receives only the strongest relevant profile per paper.

The daily selection process uses separate admission boundaries for mature papers
and papers from the last 24 months. Their guides are 475 mature and 175 recent
papers, including the learning anchors in each group. This prevents new-paper
reputation from overwhelming historical citation evidence. Category cutoffs multiply
each group's base by:

| Category | Factor |
| --- | ---: |
| Systems | 0.25 |
| Robotics | 0.90 |
| Benchmarks | 0.95 |
| Text / LLMs | 1.00 |
| Vision | 1.10 |

Lower factors mean easier admission. This preserves the larger systems collection.
The factors are policy choices, not empirical measures of field importance.
Each group's cutoff stays unchanged while its connected count is within 8% of the
guide. Outside that band the updater recalibrates toward the guide. This lets the
graph grow and shrink as papers qualify or lose relative relevance, without forcing
exactly 650 nodes on every run. The scoring equation does not normalize against
other papers, and missing evidence never pads the count. Current boundaries are
recorded in `selectionCalibration.cohorts` and `policy.cohortBaseCutoffs`.

Only the named narrative anchors, protected foundations, and their explicit
prerequisites are reserved (43 papers when this policy was introduced). Historical
or curated status alone no longer guarantees admission. All other papers, including
previously archived ones, compete using their group's category cutoff and must have
a citation or curated prerequisite connection to another visible paper. Prerequisites
remain dashed and distinct from measured citations. Hidden papers stay tracked and
can qualify later; the small learning core keeps the map usable for background study.

Discovery includes `cs.AR`, `cs.PL`, and `cs.OS` alongside the original AI,
distributed computing, and performance categories. Systems classification now
recognizes speculative decoding, prefill, disaggregation, collective communication,
CUDA/Triton, and training systems. Explicit author affiliations supplied by arXiv
are retained for the reputation calculation; absent affiliations remain unknown.

API references: [arXiv](https://info.arxiv.org/help/api/user-manual.html),
[Semantic Scholar](https://api.semanticscholar.org/api-docs/graph).
