#!/usr/bin/env python3
"""Refresh the research catalog and build the static site. Python standard library only."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NOW = datetime.now(timezone.utc)
STAMP = NOW.isoformat()
ARXIV = "https://export.arxiv.org/api/query"
S2 = "https://api.semanticscholar.org/graph/v1/paper/batch"
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "arxiv": "http://arxiv.org/schemas/atom"}
DOMAINS = {"text", "vision", "robotics", "systems", "benchmarks"}
HEADERS = {"User-Agent": "StartsAtAttention/1.0 (research metadata; github.com/harshtomarcode/starts-at-attention)"}


def request_bytes(request):
    """Respect transient service failures and shared public API throttling."""
    for attempt in range(3):
        try:
            with urlopen(request, timeout=45) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            retry = error.headers.get("Retry-After", "")
            delay = float(retry) if retry.isdigit() else 8 * (attempt + 1)
            if delay > 45:
                raise
            time.sleep(max(delay, 3))
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def validate(catalog):
    ids = set()
    for paper in catalog["papers"]:
        if not paper.get("id") or paper["id"] in ids:
            raise ValueError("Missing or duplicate paper identity")
        ids.add(paper["id"])
        datetime.strptime(paper["date"], "%Y-%m-%d")
        if paper["category"] not in DOMAINS or not paper["title"]:
            raise ValueError("Invalid paper category or title: " + paper["id"])
        count = paper.get("citationCount")
        if count is not None and (not isinstance(count, int) or count < 0):
            raise ValueError("Invalid citation count: " + paper["id"])
    cutoff = catalog["policy"].get("cutoff")
    if cutoff is not None and (not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff) or not 0 <= cutoff <= 100):
        raise ValueError("The optional cutoff must be a number from 0 to 100")
    for category, threshold in catalog["policy"].get("categoryCutoffs", {}).items():
        if category not in DOMAINS or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
            raise ValueError("Invalid category cutoff")
    for key in ("citationScale", "recentMonths", "authorHIndexScale"):
        value = catalog["policy"][key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid scoring policy: " + key)
    policy = catalog["policy"]
    if not isinstance(policy["targetVisible"], int) or policy["targetVisible"] < 1:
        raise ValueError("Invalid visible-paper target")
    if not 0 < policy["minimumCutoff"] <= 100:
        raise ValueError("Invalid minimum cutoff")
    if set(policy["categoryFactors"]) != DOMAINS or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in policy["categoryFactors"].values()):
        raise ValueError("Every category needs a positive threshold factor")
    for author in catalog["policy"].get("prominentAuthors", []):
        if not author.get("name") or not 0 <= author.get("weight", 0) <= 1 or not author.get("sourcePaperIds"):
            raise ValueError("Prominent authors need a name, bounded weight, and evidence")


def classify(title, abstract, categories):
    text = (title + " " + abstract).lower()
    if any(term in title.lower() for term in ("benchmark", "evaluating", "evaluation", "measuring", "assessing")):
        return "benchmarks"
    if "cs.RO" in categories or any(term in text for term in ("robotic", "robot manipulation", "embodied")):
        return "robotics"
    if any(term in title.lower() for term in ("gpu", "kernel", "serving", "parallelism", "quantization", "inference optimization", "inference acceleration", "distributed training", "kv cache", "kv-cache", "memory-efficient", "compiler", "speculative decoding", "speculative sampling", "prefill", "disaggregat", "all-reduce", "all-to-all", "collective communication", "cuda", "triton", "tensor program", "training system", "llm system")):
        return "systems"
    if "cs.CV" in categories or any(term in title.lower() for term in ("vision", "image", "visual", "video", "multimodal", "diffusion")):
        return "vision"
    return "text"


def discover(catalog, limit):
    """Resume a bounded date window so a daily cap never silently loses older results."""
    cursor = catalog.get("discoveryCursor")
    if cursor:
        begin, end, offset = cursor["begin"], cursor["end"], cursor["offset"]
    else:
        last = catalog.get("lastDiscoveryAt")
        begin_date = (datetime.fromisoformat(last) if last else NOW - timedelta(days=4)) - timedelta(days=3)
        begin, end, offset = begin_date.strftime("%Y%m%d0000"), NOW.strftime("%Y%m%d2359"), 0
    category_query = " OR ".join("cat:" + c for c in ("cs.CL", "cs.LG", "cs.CV", "cs.RO", "cs.AI", "cs.DC", "cs.PF", "cs.AR", "cs.PL", "cs.OS"))
    query = "(" + category_query + ") AND submittedDate:[" + begin + " TO " + end + "]"
    known = {paper["id"]: paper for paper in catalog["papers"]}
    processed = added = 0
    total = offset + 1
    while processed < limit and offset < total:
        amount = min(100, limit - processed)
        params = {"search_query": query, "start": offset, "max_results": amount, "sortBy": "submittedDate", "sortOrder": "ascending"}
        root = ET.fromstring(request_bytes(Request(ARXIV + "?" + urlencode(params), headers=HEADERS)))
        total = int(root.findtext("o:totalResults", "0", NS))
        entries = root.findall("a:entry", NS)
        if not entries and offset < total:
            raise ValueError("arXiv returned an incomplete result page")
        for entry in entries:
            url = entry.findtext("a:id", "", NS)
            identity = re.sub(r"v\d+$", "", url.rsplit("/abs/", 1)[-1])
            title = " ".join(entry.findtext("a:title", "", NS).split())
            abstract = " ".join(entry.findtext("a:summary", "", NS).split())
            published = entry.findtext("a:published", "", NS)[:10]
            categories = [node.get("term", "") for node in entry.findall("a:category", NS)]
            affiliations = sorted({" ".join(node.text.split()) for node in entry.findall("a:author/arxiv:affiliation", NS) if node.text})
            if not identity or not published or not title:
                raise ValueError("Incomplete arXiv identity metadata")
            # Broad discovery, with a relevance check for general CS/ML categories.
            relevant = any(c in categories for c in ("cs.CL", "cs.CV", "cs.RO"))
            relevant |= any(word in (title + " " + abstract).lower() for word in (
                "transformer", "language model", "neural network", "deep learning", "gpu",
                "attention", "foundation model", "mixture of experts", "mixture-of-experts",
                "reinforcement learning", "diffusion", "benchmark", "inference", "training",
                "cuda", "triton", "tensor compiler", "tensor program", "matrix multiplication"))
            if not relevant:
                continue
            paper = known.get(identity)
            if paper:
                paper.update({"abstract": abstract, "title": title,
                              "authors": [node.findtext("a:name", "", NS) for node in entry.findall("a:author", NS)],
                              "metadataUpdatedAt": STAMP})
                if affiliations:
                    paper.update({"lab": " / ".join(affiliations), "affiliationSource": "https://arxiv.org/abs/" + identity})
                continue
            paper = {
                "id": identity, "title": title, "shortTitle": title,
                "date": published, "dateBasis": "First arXiv submission",
                "category": classify(title, abstract, categories), "tags": ["new research"],
                "family": None, "lab": " / ".join(affiliations) or None,
                "authors": [node.findtext("a:name", "", NS) for node in entry.findall("a:author", NS)],
                "source": "https://arxiv.org/abs/" + identity, "abstract": abstract,
                "summary": "", "why": "Newly discovered paper; editorial review is pending.",
                "citationCount": None, "score": None, "curated": False, "protected": False,
                "status": "candidate", "prerequisites": [], "discoveredAt": STAMP,
                "metadataSource": ARXIV, "metadataUpdatedAt": STAMP,
            }
            if affiliations:
                paper["affiliationSource"] = paper["source"]
            catalog["papers"].append(paper)
            known[identity] = paper
            added += 1
        offset += len(entries)
        processed += len(entries)
        if offset < total and processed < limit:
            time.sleep(3.1)
    complete = offset >= total
    catalog["discoveryCursor"] = None if complete else {"begin": begin, "end": end, "offset": offset}
    if complete:
        catalog["lastDiscoveryAt"] = datetime.strptime(end[:8], "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
    catalog["discovery"] = {"source": "arXiv", "scanned": processed, "added": added, "windowComplete": complete, "totalInWindow": total, "at": STAMP}
    print("Discovery: scanned", processed, "records, added", added, "candidates;", "window complete" if complete else "will resume at " + str(offset))


def refresh_citations(catalog):
    papers = [p for p in catalog["papers"] if p.get("semanticScholarId") or re.fullmatch(r"\d{4}\.\d{4,5}", p["id"])]
    # Rotate through the collection when the shared provider stops a run early.
    papers.sort(key=lambda p: (p.get("citationCheckedAt", ""), -(p.get("citationCount") or 0)))
    for paper in papers:
        paper["citationFresh"] = False
    headers = {**HEADERS, "Content-Type": "application/json"}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    fields = "title,year,publicationDate,externalIds,citationCount,references.externalIds,references.title,url,authors"
    matched = checked = 0
    rate_limited = False
    provider_error = None
    for start in range(0, len(papers), 50):
        batch = papers[start:start + 50]
        ids = [p.get("semanticScholarId") or "ARXIV:" + p["id"] for p in batch]
        payload = json.dumps({"ids": ids}).encode()
        try:
            results = json.loads(request_bytes(Request(S2 + "?" + urlencode({"fields": fields}), data=payload, headers=headers)))
            if not isinstance(results, list) or len(results) != len(batch) or any(r is not None and not isinstance(r, dict) for r in results):
                provider_error = "Unexpected citation response: " + type(results).__name__ + ", length " + str(len(results) if hasattr(results, "__len__") else "unknown")
                break
        except HTTPError as error:
            rate_limited = error.code == 429
            provider_error = "Citation provider HTTP " + str(error.code)
            break
        except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
            provider_error = "Citation provider unavailable: " + str(error)
            break
        for paper, result in zip(batch, results):
            paper["citationCheckedAt"] = STAMP
            checked += 1
            if not result:
                paper["citationFresh"] = False
                continue
            ext = result.get("externalIds") if isinstance(result.get("externalIds"), dict) else {}
            provider_id = result.get("paperId")
            pinned_match = paper.get("semanticScholarId") == provider_id
            arxiv_match = ext.get("ArXiv") == paper["id"]
            contradictory_arxiv = ext.get("ArXiv") and ext["ArXiv"] != paper["id"]
            if contradictory_arxiv or not (pinned_match or arxiv_match):
                paper["citationFresh"] = False
                print("Retained prior data for identity mismatch:", paper["id"])
                continue
            count = result.get("citationCount")
            if not isinstance(count, int) or count < 0:
                paper["citationFresh"] = False
                continue
            # A sudden collapse is often an index merge/version mismatch, not loss of influence.
            prior = paper.get("citationCount")
            if prior and prior > 100 and count < prior * .1:
                paper["citationFresh"] = False
                print("Retained prior data for anomalous citation drop:", paper["id"])
                continue
            paper.update({"semanticScholarId": provider_id, "citationCount": count,
                          "citationProvider": "Semantic Scholar", "citationUpdatedAt": STAMP,
                          "citationSourceUrl": result.get("url"), "citationFresh": True})
            if isinstance(result.get("authors"), list):
                paper["semanticScholarAuthors"] = [a for a in result["authors"] if isinstance(a, dict) and a.get("authorId") and a.get("name")]
                paper["authorIdentitiesUpdatedAt"] = STAMP
            if isinstance(result.get("references"), list) and result["references"]:
                references = [r for r in result["references"] if isinstance(r, dict)]
                paper["referencedPaperIds"] = [r["paperId"] for r in references if r.get("paperId")]
                paper["referencedArxivIds"] = [r["externalIds"]["ArXiv"] for r in references if isinstance(r.get("externalIds"), dict) and r["externalIds"].get("ArXiv")]
                paper["referenceEvidenceComplete"] = True
            matched += 1
        print("Citation progress:", checked, "checked of", len(papers), flush=True)
        if start + 50 < len(papers):
            time.sleep(3.1)
    if provider_error:
        print("::warning::" + provider_error + ". Prior evidence retained; the next run starts with unchecked papers.")
    catalog["refresh"] = {"provider": "Semantic Scholar", "matched": matched, "checked": checked, "requested": len(papers), "complete": matched == len(papers), "rateLimited": rate_limited, "error": provider_error, "remaining": len(papers) - checked, "at": STAMP}
    print("Citation refresh:", matched, "matched of", len(papers), "requested; unmatched records retain prior evidence.")


def refresh_author_reputation(catalog):
    """Refresh publication-linked author profiles weekly; retain partial evidence."""
    recent = [p for p in catalog["papers"] if p.get("semanticScholarId") and
              (NOW.date() - datetime.strptime(p["date"], "%Y-%m-%d").date()).days < catalog["policy"]["recentMonths"] * 30.4375]
    stale = (NOW - timedelta(days=7)).isoformat()
    headers = {**HEADERS, "Content-Type": "application/json"}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    missing = [p for p in recent if p.get("authorIdentitiesUpdatedAt", "") < stale]
    cache = catalog.setdefault("authorMetrics", {})
    matched = 0
    error = None
    for start in range(0, len(missing), 100):
        batch = {p["semanticScholarId"]: p for p in missing[start:start + 100]}
        try:
            rows = json.loads(request_bytes(Request(S2 + "?fields=externalIds,authors", data=json.dumps({"ids": list(batch)}).encode(), headers=headers)))
            if not isinstance(rows, list):
                raise ValueError("Unexpected paper-author response")
            for row in rows:
                if not isinstance(row, dict) or row.get("paperId") not in batch:
                    continue
                paper = batch[row["paperId"]]
                arxiv = (row.get("externalIds") or {}).get("ArXiv")
                if arxiv and arxiv != paper["id"]:
                    continue
                if isinstance(row.get("authors"), list):
                    paper["semanticScholarAuthors"] = [a for a in row["authors"] if isinstance(a, dict) and a.get("authorId") and a.get("name")]
                    paper["authorIdentitiesUpdatedAt"] = STAMP
            print("Author identity progress:", min(start + 100, len(missing)), "of", len(missing), flush=True)
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError) as exc:
            error = str(exc)
            break
        if start + 100 < len(missing):
            time.sleep(3.1)
    ids = sorted({a["authorId"] for p in recent for a in p.get("semanticScholarAuthors", []) if cache.get(a["authorId"], {}).get("updatedAt", "") < stale})
    # Author profiles are looked up by IDs attached to these publications, never by a name search.
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        try:
            url = "https://api.semanticscholar.org/graph/v1/author/batch?fields=name,hIndex,citationCount,paperCount,url"
            rows = json.loads(request_bytes(Request(url, data=json.dumps({"ids": batch}).encode(), headers=headers)))
            if not isinstance(rows, list):
                raise ValueError("Unexpected author-profile response")
            for author in rows:
                if not isinstance(author, dict) or author.get("authorId") not in batch or not isinstance(author.get("hIndex"), int) or author["hIndex"] < 0:
                    continue
                cache[author["authorId"]] = {**author, "updatedAt": STAMP}
                matched += 1
            print("Author profile progress:", min(start + 500, len(ids)), "of", len(ids), flush=True)
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError) as exc:
            error = str(exc)
            break
        if start + 500 < len(ids):
            time.sleep(3.1)
    catalog["authorRefresh"] = {"at": STAMP, "requestedProfiles": len(ids), "matchedProfiles": matched, "cachedProfiles": len(cache), "error": error}
    if error:
        print("::warning::Author evidence refresh incomplete; saved profiles retained:", error)


def consequentiality(paper, policy, author_metrics=None):
    """Crossfade from author/company evidence at release to citations at maturity."""
    age = max(0, (NOW.date() - datetime.strptime(paper["date"], "%Y-%m-%d").date()).days / 30.4375)
    citation_weight = min(1, age / policy["recentMonths"])
    reputation_weight = 1 - citation_weight
    labs = []
    # Company credit needs a sourced affiliation or exact corporate author; titles do not.
    if paper.get("affiliationSource"):
        labs = [lab for lab in policy["relevantLabs"] if re.search(
            r"(?<!\w)" + re.escape(lab.casefold()) + r"(?!\w)", (paper.get("lab") or "").casefold())]
    names = {" ".join(name.casefold().split()) for name in paper.get("authors", [])}
    if paper.get("metadataSource"):
        labs = sorted(set(labs) | {lab for lab in policy["relevantLabs"] if lab.casefold() in names})
    authors = [a for a in policy.get("prominentAuthors", []) if names.intersection(
        " ".join(n.casefold().split()) for n in [a["name"]] + a.get("aliases", []))]
    metrics = author_metrics or {}
    publication_names = {re.sub(r"\W+", "", name) for name in names}
    indexed = [metrics[a["authorId"]] for a in paper.get("semanticScholarAuthors", []) if a.get("authorId") in metrics
               and re.sub(r"\W+", "", a["name"].casefold()) in publication_names
               and re.sub(r"\W+", "", metrics[a["authorId"]]["name"].casefold()) in publication_names]
    strongest = max(indexed, key=lambda a: (a["hIndex"], a["authorId"]), default=None)
    signals = ([1] if labs else []) + [a["weight"] for a in authors]
    if strongest:
        signals.append(min(1, strongest["hIndex"] / policy["authorHIndexScale"]))
    reputation_signal = 100 * max(signals) if signals else None
    citations = paper.get("citationCount")
    citation_signal = None if citations is None else min(100, 100 * citations / policy["citationScale"])
    citation_points = None if citation_signal is None else citation_weight * citation_signal
    reputation_points = None if reputation_signal is None else reputation_weight * reputation_signal
    has_citations = citation_signal is not None and citation_weight > 0
    has_reputation = reputation_signal is not None and reputation_weight > 0
    score = min(100, (citation_points or 0) + (reputation_points or 0)) if has_citations or has_reputation else None
    return {"score": None if score is None else round(score, 4),
            "scoreBasis": "pending" if score is None else "citations-and-reputation" if has_citations and has_reputation else "reputation-only" if has_reputation else "citations",
            "scoreComponents": {"citationPoints": None if citation_points is None else round(citation_points, 6),
                                "reputationPoints": None if reputation_points is None else round(reputation_points, 6),
                                "citationSignal": citation_signal, "reputationSignal": reputation_signal,
                                "citationWeight": round(citation_weight, 6), "reputationWeight": round(reputation_weight, 6),
                                "recentness": round(reputation_weight, 6), "ageMonths": round(age, 4),
                                "prominentLabs": labs, "prominentAuthors": [a["name"] for a in authors],
                                "indexedAuthor": strongest}}


def score_and_export(catalog):
    papers = catalog["papers"]
    by_arxiv = {p["id"]: p for p in papers}
    by_s2 = {p["semanticScholarId"]: p["id"] for p in papers if p.get("semanticScholarId")}
    for paper in papers:
        for alias in paper.get("semanticScholarAliases", []):
            by_s2[alias] = paper["id"]
    edges = {}
    for paper in papers:
        refs = set(paper.get("referencedArxivIds", []))
        refs.update(by_s2[r] for r in paper.get("referencedPaperIds", []) if r in by_s2)
        for ref in sorted(refs):
            if ref in by_arxiv and ref != paper["id"] and by_arxiv[ref]["date"] <= paper["date"]:
                edge = {"source": ref, "target": paper["id"], "type": "citation", "provider": "Semantic Scholar", "sourceUrl": paper.get("citationSourceUrl")}
                edges[(ref, paper["id"], "citation")] = edge
        for prerequisite in paper.get("prerequisites", []):
            identity = prerequisite if isinstance(prerequisite, str) else prerequisite["id"]
            if identity in by_arxiv and identity != paper["id"]:
                edges[(identity, paper["id"], "prerequisite")] = {"source": identity, "target": paper["id"], "type": "prerequisite", "reason": prerequisite.get("reason", "Curated prerequisite") if isinstance(prerequisite, dict) else "Curated prerequisite"}
    previous = {p["id"]: (p.get("score"), p.get("status", "candidate")) for p in papers}
    policy = catalog["policy"]
    fixed, reviewed = set(), set()
    for paper in papers:
        paper.update(consequentiality(paper, policy, catalog.get("authorMetrics", {})))
        recent = paper["scoreComponents"]["ageMonths"] < policy["recentMonths"]
        filtered = recent or paper.get("selectionPolicy") == "recent-score"
        if paper.get("protected") or (not filtered and paper.get("status") != "archived"):
            fixed.add(paper["id"])
        elif recent and policy.get("allowReviewedRecent") and paper.get("curated") and paper.get("why") and paper.get("source"):
            reviewed.add(paper["id"])
        if filtered:
            paper["selectionPolicy"] = "recent-score"
    factors = policy["categoryFactors"]
    optional = [p for p in papers if p["id"] not in fixed | reviewed and p.get("selectionPolicy") == "recent-score" and p["score"] is not None]

    def admitted(base):
        thresholds = {category: round(min(100, base * factor), 6) for category, factor in factors.items()}
        eligible = fixed | reviewed | {p["id"] for p in optional if p["score"] >= thresholds[p["category"]]}
        connected = {endpoint for e in edges.values() if e["source"] in eligible and e["target"] in eligible
                     for endpoint in (e["source"], e["target"])}
        return fixed | (eligible & connected)

    # Find the category-adjusted score boundary closest to the visible-paper target.
    # This changes admission, never the underlying score or missing evidence.
    levels = sorted({policy["minimumCutoff"], 100.0} | {
        round(max(policy["minimumCutoff"], min(100, p["score"] / factors[p["category"]])), 6) for p in optional})
    low, high = 0, len(levels) - 1
    best = None
    while low <= high:
        mid = (low + high) // 2
        base = levels[mid]
        selected = admitted(base)
        count = len(selected)
        quality = (abs(count - policy["targetVisible"]), count > policy["targetVisible"], -base)
        if best is None or quality < best[0]:
            best = (quality, base, selected)
        if count > policy["targetVisible"]:
            low = mid + 1
        else:
            high = mid - 1
    cutoff, visible = best[1], best[2]
    policy["cutoff"] = cutoff
    policy["categoryCutoffs"] = {category: round(min(100, cutoff * factor), 6) for category, factor in factors.items()}
    for paper in papers:
        paper["status"] = "active" if paper["id"] in visible else "archived"
        if paper["id"] in fixed:
            paper["selectionBasis"] = "foundation" if paper.get("protected") else "historical"
            continue
        threshold = policy["categoryCutoffs"][paper["category"]]
        meets_score = paper["score"] is not None and paper["score"] >= threshold
        paper["selectionThreshold"] = threshold
        paper["selectionBasis"] = "score" if meets_score else "editorial" if paper["id"] in reviewed else "pending"
        paper["selectionReason"] = ("No connection to the visible graph yet" if (meets_score or paper["id"] in reviewed) and paper["id"] not in visible
                                    else "Meets category cutoff" if meets_score else "Reviewed contribution to the learning path" if paper["id"] in reviewed
                                    else "Awaiting qualifying citation or reputation evidence" if paper["score"] is None else "Below category cutoff")
    catalog["selectionCalibration"] = {"at": STAMP, "targetVisible": policy["targetVisible"], "visibleCount": len(visible),
                                       "baseCutoff": cutoff, "categoryCutoffs": policy["categoryCutoffs"],
                                       "categoryCounts": {category: sum(p["id"] in visible and p["category"] == category for p in papers) for category in sorted(DOMAINS)}}
    changes = [{"id": p["id"], "score": p["score"], "previousScore": previous[p["id"]][0],
                "status": p["status"], "previousStatus": previous[p["id"]][1]}
               for p in papers if previous[p["id"]] != (p["score"], p["status"])]
    catalog["updatedAt"] = STAMP
    catalog["scoreDescription"] = (
        "Score = citationWeight × citationSignal + reputationWeight × reputationSignal. "
        "Citation weight rises linearly from 0 at release to 1 at " + str(policy["recentMonths"]) + " months; reputation weight is its complement. "
        "Citation signal = min(100, citations × 100 / " + str(policy["citationScale"]) + "). "
        "Reputation uses the strongest sourced company affiliation, curated author, or publication-linked author h-index / " + str(policy["authorHIndexScale"]) + ", scaled to 100; signals do not stack. "
        "Missing signals remain unknown and their weights are not reassigned. "
        "Category cutoffs are recalibrated toward " + str(policy["targetVisible"]) + " visible papers. "
        "Historical selections, protected foundations and reviewed recent learning contributions remain available; recent nodes require a citation or curated prerequisite connection.")
    catalog["papers"].sort(key=lambda p: (p["date"], p["id"]))
    details = {}
    index = []
    for paper in catalog["papers"]:
        year = paper["date"][:4]
        details.setdefault(year, {})[paper["id"]] = {"abstract": paper.get("abstract"), "metadataSource": paper.get("metadataSource"), "dateBasis": paper.get("dateBasis"), "affiliationSource": paper.get("affiliationSource")}
        drop = {"abstract", "referencedPaperIds", "referencedArxivIds", "semanticScholarAliases", "metadataSource", "affiliationSource", "releaseDateSource", "referenceEvidenceComplete", "semanticScholarAuthors", "authorIdentitiesUpdatedAt"}
        record = {key: value for key, value in paper.items() if key not in drop}
        record["detailFile"] = "./data/details/" + year + ".json"
        index.append(record)
    (DATA / "details").mkdir(exist_ok=True)
    for year, records in details.items():
        (DATA / "details" / (year + ".json")).write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n")
    export = {"updatedAt": STAMP, "papers": index, "links": sorted(edges.values(), key=lambda e: (e["source"], e["target"], e["type"])),
              "scoreDescription": catalog["scoreDescription"], "refresh": catalog.get("refresh"),
              "discovery": catalog.get("discovery"), "cutoff": cutoff, "policyVersion": policy["version"],
              "recentMonths": policy["recentMonths"], "categoryCutoffs": policy.get("categoryCutoffs", {}),
              "visibleCount": sum(p["status"] != "archived" for p in papers), "selectionCalibration": catalog["selectionCalibration"]}
    (DATA / "papers.json").write_text(json.dumps(export, ensure_ascii=False, separators=(",", ":")) + "\n")
    if changes:
        with (DATA / "history.jsonl").open("a") as history:
            history.write(json.dumps({"at": STAMP, "policyVersion": catalog["policy"]["version"], "changes": changes}, separators=(",", ":")) + "\n")
    print("Exported", len(papers), "papers,", len(edges), "links,", len(changes), "score or membership changes.")


def build():
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "index.html", destination / "index.html")
    public_data = destination / "data"
    public_data.mkdir(exist_ok=True)
    shutil.copy2(DATA / "papers.json", public_data / "papers.json")
    shutil.copytree(DATA / "details", public_data / "details", dirs_exist_ok=True)
    print("Static site ready in dist/ (canonical catalog and history are not in the public export).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true", help="Discover recent arXiv papers; preserve a cursor for capped windows.")
    parser.add_argument("--refresh", action="store_true", help="Refresh Semantic Scholar citation counts and reference links.")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum arXiv records scanned this run (not a selection cutoff).")
    parser.add_argument("--build", action="store_true", help="Write the deployable static site to dist/.")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.build and not args.discover and not args.refresh:
        build()
        return 0
    catalog = json.loads((DATA / "catalog.json").read_text())
    validate(catalog)
    try:
        if args.discover:
            discover(catalog, args.limit)
        if args.refresh:
            refresh_author_reputation(catalog)
            refresh_citations(catalog)
        validate(catalog)
    except (HTTPError, URLError, TimeoutError, ValueError, ET.ParseError) as error:
        print("Update stopped; the previous catalog and graph remain unchanged:", str(error), file=sys.stderr)
        return 1
    score_and_export(catalog)
    if args.discover or args.refresh:
        catalog["lastRun"] = {"at": STAMP, "discovery": args.discover, "citationRefresh": args.refresh}
    temporary = DATA / "catalog.json.tmp"
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(DATA / "catalog.json")
    if args.build:
        build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
