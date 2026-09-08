# Starts at Attention

A research atlas from **Attention Is All You Need** to modern LLM architectures,
reasoning, vision, robotics, GPU systems, and benchmarks. One HTML page, one Python
updater, no package dependencies.

The entire page is the graph. Hover to inspect a paper, click to trace its lineage,
double-click to read the original, drag a bubble to stretch its connections, drag
the background to pan, and scroll to zoom. Double-click empty space or press Home
to fit the graph. Arrow keys move between papers; Enter opens the selected source.
Reduced-motion settings disable the ambient wobble.

## Run locally

```sh
python3 update.py --build
python3 -m http.server 4173 --bind 127.0.0.1
```

Open <http://127.0.0.1:4173/>. The deployable copy is `dist/`.

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

arXiv supplies discovery, first-submission dates, authors, and abstracts. Semantic
Scholar supplies indexed citation counts and references. Its shared unauthenticated
API can throttle requests; an optional `SEMANTIC_SCHOLAR_API_KEY` repository secret
or local environment variable improves access. Other source failures stop the update without replacing the saved collection.
A citation-provider rate limit preserves successful discovery and citation batches,
records incomplete coverage, and rotates the next run toward unchecked papers.
No score-based demotions occur during rate-limited runs. Missing matches retain
prior evidence.
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
prerequisites. Bubble area grows logarithmically with citations, with a size cap.
New discoveries remain visible as emerging candidates.

The provisional impact score combines 60% log citation count, 25% log citations per
year (minimum age six months), 10% citations from this collection, and 5% verified
lab relevance. Components are normalized within the current collection. This is a
transparent starting policy, not a claim to a universal measure of importance.

`policy.cutoff` is deliberately `null`. No automatic demotions occur until it is
set to a score from 0 to 100. When configured, fresh citation evidence drives
promotion/demotion; protected foundations remain visible. Demoted records and
their edges are retained in the canonical catalog and hidden from the default graph. Editorial review,
age-cohort comparisons, and richer paper-grounded explanations remain future work.

API references: [arXiv](https://info.arxiv.org/help/api/user-manual.html),
[Semantic Scholar](https://api.semanticscholar.org/api-docs/graph).
