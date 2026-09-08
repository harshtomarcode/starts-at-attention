# Starts at Attention — working plan

## Purpose and agreed direction

Build a shareable website for personal learning that surfaces consequential AI papers and explains how modern models evolved: their architecture, how training produces capabilities, their reasoning and evaluation, and how they are implemented efficiently. The learner is a mid-level applied data scientist who builds products with models and wants deeper expertise in LLM research and design, alongside GPU training and inference optimization.

Confirmed decisions:

- Use *Attention Is All You Need* as the entry point into a broader map. Include optional older prerequisites and relevant papers outside its citation descendants.
- Select papers through a citation-based shortlist, editorial judgment, and a modest author/lab-relevance boost.
- Prioritize personal learning while making the site shareable.
- Give LLM architecture updates and model development substantial depth, explicitly including DeepSeek papers and their background.
- Keep workload optimization and Triton/CUDA kernel engineering as destinations within a parallel systems path.
- Maintain the collection through daily discovery and updates to consequentiality scores. Support thousands of papers, with stronger visual prominence for more consequential work.
- Use a lower consequentiality cutoff for active graph membership. Recent score cutoffs are 0.1 generally and 0.025 for systems, with an explicit reviewed-paper route.

The details below are proposed defaults, to be refined with the first paper collection.

## The map

- **Y axis:** time runs downward. Use first public release month/year, with local spacing adjustments for readability. Preserve exact dates and publication venue/year in paper details; display only the date precision supported by the source.
- **X axis:** text, vision/multimodal, robotics, systems/infra, and benchmarks. Text can expand into architecture, pretraining/data, post-training, and reasoning/context/tool use. Systems can expand into inference, training, and GPU kernels/compilers. Each paper occupies one primary lane and has additional domain and technique tags. Lab/model-family filters connect papers across topics without duplicating their nodes.
- **Nodes:** one per paper, consolidating preprint and conference versions. Bubble area grows with a logarithmic transformation of total direct incoming citations, with a minimum readable size and a maximum cap. Keep the scale stable when filtering; show the exact count, provider, and retrieval date in details.
- **Edges:** display an arrow from an earlier cited paper to a later citing paper. Multiple parents are allowed. Explain the direction in the legend: “B cites A.” Citation evidence alone does not establish that B derives its main idea from A.
- **Prerequisites:** use a separate, labeled relationship for “understand this first.” Keep older foundations collapsed initially and allow concept notes where a full paper would be unnecessary.
- **Density:** support thousands of papers through progressive detail. At overview scale, prioritize consequential landmarks and their labels; reveal more qualifying nodes and relationships when zooming, filtering, or selecting a neighborhood. Papers hidden at the current zoom remain members of the graph. Papers below the cutoff are excluded from the active graph and remain available in history.

Attention is the initial focus. The graph can contain independent roots and cross-links; relevant architectural, learning, and systems foundations do not have to connect artificially to Attention. Chronological position expresses time, not the number of citation hops from the entry paper.

## Selecting consequential papers

Use the consequentiality score for bubble size, with citation points plus a temporary reputation bonus for recent work. Treat transitive descendants as a distinct potential influence measure, not as additional direct citations.

Selection proceeds in three passes:

1. Assemble candidates from citation neighborhoods, domain searches, and known foundational papers. Compare citation impact within similar publication ages and topics using the broader candidate pool, rather than only the displayed papers.
2. Review the shortlist for downstream uptake, relevance to the learning goals, and author/lab relevance. Verify use or extension of an idea before calling a citation evidence of substantive influence. Prefer publication-time affiliations over an author's current employer.
3. Admit editorial exceptions for prerequisites and emerging contributions, and record a short reason. Keep “established impact” and “promising recent work” distinguishable.

Citation impact is the main signal; author/lab relevance is a modest adjustment that fades over two years. The implemented formula and cutoff are recorded in README.md and the versioned catalog policy. Select for distinct changes to architecture, learning methods, capabilities, and execution. Ensure the shortlist covers the breadth of LLM development as well as systems, benchmarks, and the other domains.

Preserve a distinction between enduring influence and recent momentum. The score should allow established work to remain consequential and newer work to gain prominence. Increased attention alone is provisional evidence, and low citation counts in newly released papers do not settle their eventual importance.

Use capped square-root consequentiality radii for bubble size. Use the same score for recent-paper admission, ranking, and label priority. Display the components and freshness of the score on selection. Node weight does not change what a citation edge means.

## Daily discovery and maintenance

The long-term product is a continuously maintained graph with potentially thousands of qualifying papers. The small pilot below is an audit and interaction-design sample, not a permanent limit on collection size.

Each daily update should:

1. **Discover:** scan new papers and updates from primary paper repositories, conference sources, and official research groups across all agreed domains, including DeepSeek and other relevant labs. Search beyond direct citations of Attention. Use an overlapping date window and the last successful scan to catch late indexing and missed runs.
2. **Consolidate:** resolve canonical paper identities and versions; add new candidates and enrich existing records without duplicating papers or summing overlapping citation counts.
3. **Refresh evidence:** update citation metrics across the tracked catalog in batches and refresh relevant citation relationships. Retain earlier measurements when a source is unavailable and mark them stale; a failed refresh must not erase known counts. New candidates with no qualifying evidence remain hidden.
4. **Recalculate:** recompute consequentiality for the full tracked catalog using the saved scoring policy and latest available evidence. Retain score inputs, dates, and policy version so gains and losses can be explained. Flag incomplete source coverage.
5. **Apply membership:** promote qualifying papers and remove papers that fall below the configured exit rule from the active graph. Retain their metadata, history, and verified edges for inspection and possible re-entry. The current thresholds are 0.1 generally and 0.025 for systems, with reviewed recent contributions and protected foundations retained.
6. **Update the site dataset:** generate a coherent graph update and a concise record of additions, demotions, and meaningful score changes. Retain the last complete graph if discovery or scoring is incomplete. Publish through the site's established update mechanism once one exists.

New papers first enter a candidate pool. An explicitly labeled emerging-paper route can admit promising work before citation evidence accumulates; its current route is the two-year reputation bonus and cutoff described in README.md. Citation gains and newly indexed evidence can later move a candidate into the established graph.

Consider separate entry/exit boundaries or a period below the cutoff to avoid repeated removal and re-entry caused by small fluctuations. These parameters remain undecided. The Attention entry point and explicitly curated prerequisite material remain available as labeled foundations; this does not inflate their measured scores.

If an intermediate paper leaves the active graph, preserve the original relationships in history. Do not connect its neighbors with a fabricated direct citation. Any displayed collapsed path must be labeled as indirect.

The GitHub daily updater is running. The older Codex daily task, `daily-research-graph-update`, is paused with explicit user approval to avoid duplicate scans. GitHub maintains the complete catalog, rescoring and applying membership rules on each successful update.

The user wants to explore free cloud execution. GitHub Actions is the proposed hosted alternative: run a small daily metadata/scoring script on a standard Linux runner and write the refreshed dataset for the website. Standard hosted runner use is free for public repositories; GitHub Free includes 2,000 monthly minutes for private repositories, shared across account usage. A 10-minute daily job would consume about 300 minutes over 30 days; this is a planning estimate, not a measured runtime. [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

Free paper discovery and metadata refresh can use arXiv and an OpenAlex free API key within their allowances. Saved editorial judgments can supplement deterministic scoring; paid model calls are optional and are not included in GitHub's runner allowance. GitHub Pages is a potential free host for the static website when using a public repository. [arXiv API terms](https://info.arxiv.org/help/api/tou.html), [OpenAlex pricing](https://help.openalex.org/access/pricing/), [GitHub Pages availability](https://docs.github.com/en/pages/getting-started-with-github-pages)

Codex already provides built-in scheduling. The task created here operates on the local project and requires the computer and desktop app to be running. GitHub Actions would execute on GitHub's infrastructure. The GitHub Actions updater is implemented in `.github/workflows/update-papers.yml` for daily execution at 12:23 UTC. The hosted site reads the committed graph and abstract shards directly from the GitHub repository, with a bundled snapshot fallback. Once its first cloud run succeeds, pause the duplicate local Codex scan. [Official scheduled-task documentation](https://learn.chatgpt.com/docs/automations?surface=app)

## Learning paths and research coverage

The map preserves historical chronology. A separate learning overlay supplies prerequisite-based reading orders through several branches. Shared Transformer foundations lead into both model research and systems engineering; kernel engineering is one possible destination.

| Branch | Main concepts | Evidence of understanding |
| --- | --- | --- |
| Model architecture | Transformer foundations, attention variants, positional representations, dense and mixture-of-experts models, routing, recurrent/hybrid designs | Explain what changed between architectures, why it changed, and the quality, capacity, and compute tradeoffs. |
| Pretraining and data | Objectives, data quality/composition, synthetic data, scaling laws, compute allocation | Explain how model size, data, objectives, and training budget influence capabilities. |
| Adaptation and post-training | Fine-tuning, preference learning, reinforcement learning, rewards, verifiers, distillation | Distinguish architectural changes from changes to learned behavior and explain the evidence for each. |
| Reasoning and inference-time methods | Reasoning strategies, additional inference computation, search, long context, retrieval, tool use | Explain where a capability comes from and compare approaches under explicit quality and cost constraints. |
| Training and inference systems | GPU performance, precision, memory, KV caches, batching, checkpointing, parallelism, communication, kernels | Predict and measure bottlenecks, optimize workloads, then implement and profile small Triton/CUDA kernels. |
| Vision, multimodality, and robotics | Visual representations, language/vision integration, action representations, learning from environments | Explain how ideas move between domains and which new problems arise. |
| Evaluation | Capability benchmarks, robustness, contamination, evaluator reliability, quality/cost comparisons | Assess whether a reported improvement is supported and relevant to a concrete use case. |

These branches are exploration lenses within the requested high-level lanes. Benchmark papers retain their own lane and relevant domain tags. Cross-links should help a learner connect a model-design decision to its training consequences, measured capabilities, and execution costs.

DeepSeek is an explicit example of a model family spanning several branches. Candidate anchors include [DeepSeekMoE](https://arxiv.org/abs/2401.06066), [DeepSeekMath](https://arxiv.org/abs/2402.03300), [DeepSeek-V2](https://arxiv.org/abs/2405.04434), [DeepSeek-V3](https://arxiv.org/abs/2412.19437), and [DeepSeek-R1](https://arxiv.org/abs/2501.12948). These are examples for the pilot, not an exhaustive or latest-release list. Include the earlier ideas needed to understand them and relevant work from other groups.

For example, DeepSeek-V2 provides an anchor for Multi-head Latent Attention and mixture-of-experts architecture. DeepSeek-V3 connects these architectural choices to load balancing, multi-token prediction, and the training recipe. DeepSeek-R1 provides an anchor for reinforcement learning and reasoning. The proposed learning connections require content review; this grouping does not assert citation edges between every listed paper.

Check tensor-level mechanics, learning objectives, and architecture fundamentals with short exercises instead of assuming either beginner or expert knowledge. Give explanations an intuitive entry point with optional mathematical and implementation depth. A learner should be able to explain a model report's design choices and supporting evidence without first completing the GPU engineering branch.

For later lessons, each paper should explain the previous limitation, prerequisite concepts, new idea, mechanism, tradeoffs, and subsequent developments. Attach hardware and workload conditions to performance claims. Learning dependencies require content review; do not infer them solely from citation links.

## First release and sequence

1. **Paper pilot:** use roughly 60 papers to validate evidence, scoring, and graph interactions. Include substantial LLM architecture, pretraining, post-training, and reasoning coverage, explicit DeepSeek anchors and their foundations, a deep systems branch, and landmarks in vision, robotics, and benchmarks. Record why each paper belongs and verify the connections that will be displayed. Review coverage by research contribution so systems prerequisites do not dominate the collection.
2. **Historical expansion and daily updater:** backfill relevant literature from 2017 onward and required older foundations; grow toward thousands of papers as justified by relevance and the eventual cutoff. Implement discovery, metadata refresh, full-catalog scoring, and membership history. Compare candidate cutoff policies on real distributions before enabling automatic demotions.
3. **Interactive map at scale:** build the chronological lanes, capped bubbles, node selection, paper details, search, category filtering, and neighborhood expansion. Include brief contribution and prerequisite notes. Verify readability and responsiveness with thousands of nodes through progressive detail and viewport filtering, while preserving architectural evolution and cross-domain connections.
4. **Learning depth:** add the guided reading overlay, progressively deeper explanations, and practical checkpoints. Develop two substantial routes: understanding modern LLM design and training, including DeepSeek, and understanding efficient training/serving with optional kernel exercises. Connect the routes where architectural choices affect execution and where systems constraints motivate model design.

The first release is successful when the learner can find an important paper, understand why it belongs, inspect its citation relationships, identify relevant background, and follow a useful next step. The pilot should support following an architecture or reasoning idea through several papers as well as tracing an optimization technique.

Use a small website backed by a persistent paper catalog and a daily update process. The site can read generated graph data independently of the scheduled updater. A single page is sufficient initially; use a rendering approach suitable for thousands of nodes, testing Canvas or WebGL only if the visible workload requires it. Choose hosting and the production scheduler when there is a concrete implementation to deploy; distinguish the initial Codex research schedule from the eventual deployed site's operation.

## Data and evidence

Use one consistent provider for displayed citation counts. OpenAlex and Semantic Scholar are candidates for discovering papers and references; check coverage on the pilot before choosing the primary provider. Verify important edges against the papers, since metadata reference lists can be incomplete.

Keep canonical identifiers, titles, abstracts where available, authors, source links, first release dates, publication-time affiliations where available, citation counts with retrieval dates, categories, editorial selection reasons, score history, and membership changes. Preserve unknown values as unknown. Consolidate versions without summing overlapping citation counts. Check paper versions when a reference appears inconsistent with first-release chronology.

### Storage

Store abstracts and compact metadata in the GitHub repository as JSON/JSONL files. This is the durable catalog; the scheduled updater checks it out, changes the records, and commits successful updates. A runner's temporary filesystem is not the persistent store. Publish a browser-ready graph export with the website. A separate database or object-storage service is unnecessary at the initial scale.

The catalog includes the information needed beyond an abstract: IDs, titles, authors and relevant affiliations, dates, categories, citation counts, source URLs, ranking inputs, current scores, and membership status. Store graph relationships once using paper IDs, with provenance. A paper's total incoming citation count does not require storing every paper that cites it.

Planning estimates, using decimal KB/MB and excluding accumulated history:

| Papers | Abstracts at 2 KB each | Catalog budget at 5–10 KB per paper, including compact metadata and relationships |
| --- | --- | --- |
| 5,000 | 10 MB | 25–50 MB |
| 10,000 | 20 MB | 50–100 MB |

These are assumptions to check against the pilot, not measured corpus sizes. Author lists and relationship density can change the average. The likely scale for a few thousand records is tens of megabytes. Git history, generated exports, and deployment copies add to the total. Preserve stable record ordering, update only changed records, and keep compact score/membership changes rather than duplicating the full catalog every day. Split data files if their measured size warrants it.

Initial browser loading should contain the small graph index: IDs, labels, dates, categories, weights, and visible relationships. Load abstract/detail chunks when needed, so catalog size does not become the initial page-download size. GitHub Pages currently permits a published site up to 1 GB. [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)

Keep links to original paper pages and PDFs. Routine discovery and ranking should not create a PDF archive. Consult full text on demand when verifying citation context or authoring technical explanations; store the resulting curated notes and source locators. Abstracts support discovery and brief overviews, but detailed architectural explanations and prerequisite judgments need evidence from the paper itself.

References supporting the planning decisions:

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762): the June 2017 entry paper.
- [DeepSeek-V2](https://arxiv.org/abs/2405.04434): Multi-head Latent Attention and mixture-of-experts model design.
- [DeepSeek-V3](https://arxiv.org/abs/2412.19437): architecture, load balancing, multi-token prediction, and training.
- [DeepSeek-R1](https://arxiv.org/abs/2501.12948): reinforcement learning for reasoning capabilities.
- [Roofline performance model](https://amcr.lbl.gov/departments/computer-science-department/ppan/roofline-performance-model/): a foundation for reasoning about computation and memory bandwidth.
- [FlashAttention](https://arxiv.org/abs/2205.14135): connects attention computation to GPU memory movement and tiling.
- [PagedAttention / vLLM](https://arxiv.org/abs/2309.06180): connects LLM serving and KV-cache management to paging concepts.
- [OpenAlex citations](https://help.openalex.org/data/works/citations/): citation fields and reference-matching limitations.
- [Semantic Scholar API](https://www.semanticscholar.org/product/api): paper, author, citation, and reference metadata.

## Decisions to revisit after the pilot

The lower cutoff, scoring weights, entry/exit rules, emerging-paper policy, foundation exceptions, category balance, node-size cap, initial visible density, and visual styling remain adjustable. The graph has no fixed small paper-count limit. Source access, refresh coverage, and the production schedule will be validated with the updater. GPU access and a compute budget matter when practical exercises are implemented; they do not block the paper map.


## Implemented initial atlas — September 7, 2026

The first expanded build contains 2,186 real papers, including 497 established selections, 612 measured citation counts, 7,579 citation edges, and 191 labeled learning prerequisites. Dates reach September 4, 2026, the latest submissions in the completed daily discovery window. There are 2,185 actual abstracts; GPT-2's official report has no abstract stored. The canonical JSON is approximately 8.7 MB and the graph export 3.7 MB, before compression. No PDFs are retained.

The page uses Canvas with capped square-root citation radii, gentle motion, hover feedback, draggable springs, a full-window graph with no persistent interface chrome, keyboard paper navigation, and on-demand abstract shards. The user explicitly requested removal of the header, sidebar, toolbar, and details panel. The DeepSeek path includes later architecture work through V4; LLM research and GPU systems remain parallel learning strands. Deep ELI5 lessons and practical exercises are not part of this initial atlas.

The updater uses arXiv metadata plus Semantic Scholar citation evidence. OpenAlex was evaluated but is not required. Canonical Semantic Scholar IDs and explicit duplicate aliases preserve the GPipe and DeepSeek-V3 version corrections. Citation coverage remains incomplete, particularly for recent submissions. Policy version 3 lowers recent cutoffs to 0.1 generally and 0.025 for systems, and allows explicit editorial admission. Source failures preserve saved evidence and successful batches, record incomplete coverage, and rotate to unchecked papers next time. Membership uses the retained evidence, while the reputation bonus continues to age normally.

The user requested stronger bubble-size contrast after the graph-only layout. Radius now ranges from 1.4 to 18 CSS pixels at default zoom, following the square root of consequentiality and capping at 100 points. Unknown counts use a hollow bubble; a qualifying reputation-only score is labeled provisional.

The final navigation is a native scrolling timeline with roughly seven months per viewport. The category order is Vision, Robotics, Text/LLMs, Systems, Benchmarks; grid lines and the text labels across the top are removed, while bubble colors remain. Twenty-two sourced milestone captions accompany the scroll on the right. Normal scrolling moves through time; Control/Command-scroll zooms; Home returns to Attention Is All You Need. Provider HTTP, transport, malformed JSON, and malformed batch failures preserve valid earlier evidence and discovery, report incomplete coverage, and preserve earlier citation measurements during rescoring.

The user widened the scroll window to 6–8 months; the default is now seven months per viewport. The GitHub daily workflow completed successfully, refreshing 200 citation records before the shared provider rate limit. It preserves the other records and resumes with unchecked papers. The older Codex daily schedule is paused with explicit user approval; GitHub remains the daily updater.


## Recent-paper selection — policy version 2

The user requested a relatively linear score that gives promising new work from relevant companies or authors a temporary advantage, then relies on citations as papers age. The score is `min(100, citations / 1000 + 8 × prominence × max(0, 1 − age_months / 24))`. A sourced publication affiliation, exact corporate author, or curated author match supplies prominence; the two signals do not stack. The policy records the author list and landmark-paper evidence, and hover details show the score components. Unknown counts remain unknown.

Under version 2, recent papers had to score at least 4 and have a verified citation or curated prerequisite connection to another visible paper to appear. Hidden records remain tracked for citation updates and later promotion. Papers already subject to the filter must still qualify after two years, so aging alone cannot revive a hidden paper. Existing older historical selections and protected foundations remain visible. That policy initially produced 560 visible papers out of 2,186 tracked, including 14 from the last two years; the recent tail is deliberately selective and will expand as evidence improves.


## Broader recent coverage — policy version 3

The 4-point threshold admitted only 14 recent papers and excluded useful systems work. The user requested a broader recent map, more systems sourcing, looser systems thresholds, and removal of the category text across the top while retaining colors.

Keep the same linear scoring equation and two-year reputation fade. Lower admission to 0.1 points (100 citations without a reputation signal), with 0.025 points (25 citations) for systems. Explicitly reviewed recent contributions may qualify regardless of citation coverage, with a written learning rationale and source; this does not change their measured score. They still need a verified citation or separately labeled learning-prerequisite connection. The editorial route lasts only while the paper is recent. Automatic discovery never marks papers curated.

Expand discovery to hardware architecture, programming languages, and operating systems categories and retain explicit arXiv affiliations when available. Add focused primary-source coverage for serving, KV caches, speculative decoding, kernels, compilers, distributed training, MoE communication, low precision, and newer architectures and evaluations. The category names across the top are removed; domain colors, central Text/LLM lane, seven-month scroll scale, and narrative captions remain.

The September 8 expansion adds 52 papers and reviews 27 existing records. The catalog now tracks 2,238 papers and displays 663, including 108 from the last two years: 44 systems, 49 text, 4 vision, 6 robotics, and 5 benchmarks. All 52 additions have verified citation counts. Twenty recent papers enter through explicit editorial review; their scores remain based on evidence.
