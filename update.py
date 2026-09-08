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
    for key in ("citationScale", "recentMonths"):
        value = catalog["policy"][key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid scoring policy: " + key)
    bonus = catalog["policy"]["reputationBonus"]
    if not isinstance(bonus, (int, float)) or not math.isfinite(bonus) or not 0 <= bonus <= 100:
        raise ValueError("Invalid reputation bonus")
    for author in catalog["policy"].get("prominentAuthors", []):
        if not author.get("name") or not 0 <= author.get("weight", 0) <= 1 or not author.get("sourcePaperIds"):
            raise ValueError("Prominent authors need a name, bounded weight, and evidence")


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


def consequentiality(paper, policy):
    """Linear citation points plus a modest, temporary editorial reputation signal."""
    age = max(0, (NOW.date() - datetime.strptime(paper["date"], "%Y-%m-%d").date()).days / 30.4375)
    recentness = max(0, 1 - age / policy["recentMonths"])
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
    reputation = max([float(bool(labs))] + [a["weight"] for a in authors])
    bonus = policy["reputationBonus"] * recentness * reputation
    citations = paper.get("citationCount")
    citation_points = None if citations is None else min(100, 100 * citations / policy["citationScale"])
    score = None if citation_points is None and bonus == 0 else min(100, (citation_points or 0) + bonus)
    return {"score": None if score is None else round(score, 4),
            "scoreBasis": "pending" if score is None else "reputation-only" if citations is None else "citations-and-reputation" if bonus else "citations",
            "scoreComponents": {"citationPoints": citation_points, "reputationPoints": round(bonus, 4),
                                "recentness": round(recentness, 6), "ageMonths": round(age, 4),
                                "prominentLabs": labs, "prominentAuthors": [a["name"] for a in authors]}}


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
    cutoff = policy.get("cutoff")
    for paper in papers:
        old_status = paper.get("status", "candidate")
        paper.update(consequentiality(paper, policy))
        recent = paper["scoreComponents"]["ageMonths"] < policy["recentMonths"]
        # Once filtered, a paper must earn promotion; merely aging out never revives it.
        filtered_collection = recent or paper.get("selectionPolicy") == "recent-score"
        if paper.get("protected"):
            paper["status"] = "active"
        elif cutoff is not None and filtered_collection:
            paper["status"] = "active" if paper["score"] is not None and paper["score"] >= cutoff else "archived"
            paper["selectionPolicy"] = "recent-score"
            paper["selectionReason"] = "Meets recent-paper cutoff" if paper["status"] == "active" else "Awaiting qualifying citation or reputation evidence" if paper["score"] is None else "Below recent-paper cutoff"
        else:
            paper["status"] = old_status
    visible = {p["id"] for p in papers if p["status"] != "archived"}
    connected = {endpoint for e in edges.values() if e["source"] in visible and e["target"] in visible
                 for endpoint in (e["source"], e["target"])}
    for paper in papers:
        if (cutoff is not None and paper.get("selectionPolicy") == "recent-score"
                and not paper.get("protected") and paper["status"] == "active" and paper["id"] not in connected):
            paper["status"] = "archived"
            paper["selectionReason"] = "No connection to the visible graph yet"
    changes = [{"id": p["id"], "score": p["score"], "previousScore": previous[p["id"]][0],
                "status": p["status"], "previousStatus": previous[p["id"]][1]}
               for p in papers if previous[p["id"]] != (p["score"], p["status"])]
    catalog["updatedAt"] = STAMP
    catalog["scoreDescription"] = (
        "Score = min(100, citations / " + str(policy["citationScale"] / 100) + " + " + str(policy["reputationBonus"]) +
        " × prominence × max(0, 1 − age in months / " + str(policy["recentMonths"]) + ")). "
        "Prominence uses a sourced company affiliation, exact corporate author, or curated author match, whichever is higher; they do not stack. "
        "Unknown citations remain unknown; a reputation-only score is labeled provisional. "
        "Recent-paper cutoff: " + str(cutoff) + ", with at least one verified citation or curated prerequisite connection to another visible paper. "
        "Older established papers are retained; filtered papers stay hidden until they qualify.")
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
              "discovery": catalog.get("discovery"), "cutoff": cutoff, "policyVersion": policy["version"],
              "recentMonths": policy["recentMonths"], "visibleCount": sum(p["status"] != "archived" for p in papers)}
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
