#!/usr/bin/env python3
"""Refresh the research catalog and build the static site. Python standard library only."""
import argparse
from collections import Counter
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
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}
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


def classify(title, abstract, categories):
    text = (title + " " + abstract).lower()
    if any(term in title.lower() for term in ("benchmark", "evaluating", "evaluation", "measuring", "assessing")):
        return "benchmarks"
    if "cs.RO" in categories or any(term in text for term in ("robotic", "robot manipulation", "embodied")):
        return "robotics"
    if any(term in title.lower() for term in ("gpu", "kernel", "serving", "parallelism", "quantization", "inference optimization", "distributed training", "kv cache", "kv-cache", "memory-efficient", "compiler")):
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
    category_query = " OR ".join("cat:" + c for c in ("cs.CL", "cs.LG", "cs.CV", "cs.RO", "cs.AI", "cs.DC", "cs.PF"))
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
            if not identity or not published or not title:
                raise ValueError("Incomplete arXiv identity metadata")
            # Broad discovery, with a relevance check for general CS/ML categories.
            relevant = any(c in categories for c in ("cs.CL", "cs.CV", "cs.RO"))
            relevant |= any(word in (title + " " + abstract).lower() for word in (
                "transformer", "language model", "neural network", "deep learning", "gpu",
                "attention", "foundation model", "mixture of experts", "mixture-of-experts",
                "reinforcement learning", "diffusion", "benchmark", "inference", "training"))
            if not relevant:
                continue
            paper = known.get(identity)
            if paper:
                paper.update({"abstract": abstract, "title": title,
                              "authors": [node.findtext("a:name", "", NS) for node in entry.findall("a:author", NS)],
                              "metadataUpdatedAt": STAMP})
                continue
            paper = {
                "id": identity, "title": title, "shortTitle": title,
                "date": published, "dateBasis": "First arXiv submission",
                "category": classify(title, abstract, categories), "tags": ["new research"],
                "family": None, "lab": None,
                "authors": [node.findtext("a:name", "", NS) for node in entry.findall("a:author", NS)],
                "source": "https://arxiv.org/abs/" + identity, "abstract": abstract,
                "summary": "", "why": "Newly discovered paper; editorial review is pending.",
                "citationCount": None, "score": None, "curated": False, "protected": False,
                "status": "candidate", "prerequisites": [], "discoveredAt": STAMP,
                "metadataSource": ARXIV, "metadataUpdatedAt": STAMP,
            }
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
    fields = "title,year,publicationDate,externalIds,citationCount,references.externalIds,references.title,url"
    matched = checked = 0
    rate_limited = False
    for start in range(0, len(papers), 50):
        batch = papers[start:start + 50]
        ids = [p.get("semanticScholarId") or "ARXIV:" + p["id"] for p in batch]
        payload = json.dumps({"ids": ids}).encode()
        try:
            results = json.loads(request_bytes(Request(S2 + "?" + urlencode({"fields": fields}), data=payload, headers=headers)))
        except HTTPError as error:
            if error.code != 429:
                raise
            rate_limited = True
            print("::warning::Citation provider rate limited this run. Prior evidence is retained; the next run starts with papers not yet checked.")
            break
        if not isinstance(results, list) or len(results) != len(batch):
            raise ValueError("Unexpected Semantic Scholar batch response")
        for paper, result in zip(batch, results):
            paper["citationCheckedAt"] = STAMP
            checked += 1
            if not result:
                paper["citationFresh"] = False
                continue
            ext = result.get("externalIds") or {}
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
            if result.get("references"):
                references = result["references"]
                paper["referencedPaperIds"] = [r["paperId"] for r in references if r.get("paperId")]
                paper["referencedArxivIds"] = [(r.get("externalIds") or {})["ArXiv"] for r in references if (r.get("externalIds") or {}).get("ArXiv")]
                paper["referenceEvidenceComplete"] = True
            matched += 1
        print("Citation progress:", checked, "checked of", len(papers), flush=True)
        if start + 50 < len(papers):
            time.sleep(3.1)
    catalog["refresh"] = {"provider": "Semantic Scholar", "matched": matched, "checked": checked, "requested": len(papers), "complete": matched == len(papers), "rateLimited": rate_limited, "remaining": len(papers) - checked, "at": STAMP}
    print("Citation refresh:", matched, "matched of", len(papers), "requested; unmatched records retain prior evidence.")


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
    uptake = Counter(e["source"] for e in edges.values() if e["type"] == "citation")
    weights = catalog["policy"]["weights"]
    known = [p for p in papers if p.get("citationCount") is not None]
    maxima = {"citations": 1.0, "citationRate": 1.0, "graphUptake": 1.0}
    raw = {}
    for paper in known:
        years = max(.5, (NOW.date() - datetime.strptime(paper["date"], "%Y-%m-%d").date()).days / 365.25)
        relevant = any(lab.lower() in (paper.get("lab") or "").lower() for lab in catalog["policy"]["relevantLabs"])
        raw[paper["id"]] = {"citations": math.log1p(paper["citationCount"]),
                            "citationRate": math.log1p(paper["citationCount"] / years),
                            "graphUptake": math.log1p(uptake[paper["id"]]),
                            "labRelevance": float(relevant)}
        for key in maxima:
            maxima[key] = max(maxima[key], raw[paper["id"]][key])
    changes = []
    cutoff = catalog["policy"].get("cutoff")
    for paper in papers:
        old_score, old_status = paper.get("score"), paper.get("status", "candidate")
        if paper["id"] in raw:
            components = {key: round(value / maxima.get(key, 1), 5) for key, value in raw[paper["id"]].items()}
            paper["score"] = round(100 * sum(weights[key] * value for key, value in components.items()), 2)
            paper["scoreComponents"] = components
        else:
            paper["score"] = None
        # Cutoff is deliberately unset. New discoveries remain candidates until configured.
        if paper.get("protected"):
            paper["status"] = "active"
        elif cutoff is not None and not catalog.get("refresh", {}).get("rateLimited") and paper.get("citationFresh") and paper["score"] is not None:
            paper["status"] = "active" if paper["score"] >= cutoff else "archived"
        else:
            paper["status"] = old_status
        if old_score != paper["score"] or old_status != paper["status"]:
            changes.append({"id": paper["id"], "score": paper["score"], "previousScore": old_score, "status": paper["status"], "previousStatus": old_status})
    catalog["updatedAt"] = STAMP
    catalog["scoreDescription"] = "Provisional score: 60% log citation count, 25% log citations per year (minimum age six months), 10% citations from this collection, and 5% verified lab relevance. Components are normalized within the collection. Missing citations have no score. The permanent inclusion cutoff is not set." if cutoff is None else "Provisional score: 60% log citations, 25% age-adjusted citation rate, 10% in-graph uptake, 5% lab relevance. Configured inclusion cutoff: " + str(cutoff) + "."
    catalog["papers"].sort(key=lambda p: (p["date"], p["id"]))
    details = {}
    index = []
    for paper in catalog["papers"]:
        year = paper["date"][:4]
        details.setdefault(year, {})[paper["id"]] = {"abstract": paper.get("abstract"), "metadataSource": paper.get("metadataSource"), "dateBasis": paper.get("dateBasis"), "affiliationSource": paper.get("affiliationSource")}
        drop = {"abstract", "referencedPaperIds", "referencedArxivIds", "semanticScholarAliases", "metadataSource", "affiliationSource", "releaseDateSource", "referenceEvidenceComplete"}
        record = {key: value for key, value in paper.items() if key not in drop}
        record["detailFile"] = "./data/details/" + year + ".json"
        index.append(record)
    (DATA / "details").mkdir(exist_ok=True)
    for year, records in details.items():
        (DATA / "details" / (year + ".json")).write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n")
    export = {"updatedAt": STAMP, "papers": index, "links": sorted(edges.values(), key=lambda e: (e["source"], e["target"], e["type"])),
              "scoreDescription": catalog["scoreDescription"], "refresh": catalog.get("refresh"),
              "discovery": catalog.get("discovery"), "cutoff": cutoff}
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
