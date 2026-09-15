#!/usr/bin/env python3
"""Refresh the research catalog and build the static site. Python standard library only."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NOW = datetime.now(timezone.utc)
STAMP = NOW.isoformat()
S2 = "https://api.semanticscholar.org/graph/v1/paper/batch"
DOMAINS = {"text", "vision", "robotics", "systems", "benchmarks"}
HEADERS = {"User-Agent": "StartsAtAttention/1.0 (research metadata; github.com/harshtomarcode/starts-at-attention)"}
REQUEST_STATE = {}
LAST_REQUEST = {}


def request_bytes(request):
    """Pace each provider and carry a rate-limit cooldown across stages and runs."""
    host = urlsplit(request.full_url).hostname
    state = REQUEST_STATE.setdefault(host, {})
    blocked = state.get("blockedUntil")
    if blocked and datetime.fromisoformat(blocked) > datetime.now(timezone.utc):
        state["deferredThisRun"] = state.get("deferredThisRun", 0) + 1
        raise URLError(host + " is cooling down until " + blocked)
    interval = {"export.arxiv.org": 3.1, "api.datacite.org": 1.1, "api.openalex.org": 1.1,
                "api.semanticscholar.org": 1.1 if os.environ.get("SEMANTIC_SCHOLAR_API_KEY") else 6.1}.get(host, 1.1)
    for attempt in range(3):
        delay = interval - (time.monotonic() - LAST_REQUEST.get(host, -interval))
        if delay > 0:
            time.sleep(delay)
        LAST_REQUEST[host] = time.monotonic()
        state["lastAttemptAt"] = datetime.now(timezone.utc).isoformat()
        state["requestsThisRun"] = state.get("requestsThisRun", 0) + 1
        try:
            with urlopen(request, timeout=45) as response:
                result = response.read()
                state.update({"lastStatus": response.status, "lastSuccessAt": datetime.now(timezone.utc).isoformat()})
                state.pop("blockedUntil", None)
                return result
        except HTTPError as error:
            state["lastStatus"] = error.code
            retry = error.headers.get("Retry-After", "") if error.headers else ""
            try:
                delay = float(retry) if retry.isdigit() else max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                delay = 8 * (attempt + 1)
            if error.code == 429:
                state["blockedUntil"] = (datetime.now(timezone.utc) + timedelta(seconds=max(3600, delay))).isoformat()
                raise
            if delay > 45:
                state["blockedUntil"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                raise
            if error.code not in (500, 502, 503, 504) or attempt == 2:
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
    if not isinstance(policy["recentTarget"], int) or not 0 < policy["recentTarget"] < policy["targetVisible"]:
        raise ValueError("Recent-paper guide must be smaller than the overall guide")
    if not 0 <= policy["countTolerance"] <= .25 or any(identity not in ids for identity in policy["learningAnchors"]):
        raise ValueError("Invalid selection tolerance or learning anchor")
    if not 0 < policy["minimumCutoff"] <= 100:
        raise ValueError("Invalid minimum cutoff")
    if type(policy.get("monthlyVisibleLimit")) is not int or policy["monthlyVisibleLimit"] < 1:
        raise ValueError("Invalid monthly density limit")
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
    if any(term in title.lower() for term in ("gpu", "kernel", "serving", "parallelism", "quantization", "inference optimization", "inference acceleration", "distributed training", "kv cache", "kv-cache", "memory-efficient", "compiler", "speculative decoding", "speculative sampling", "prefill", "disaggregat", "all-reduce", "all-to-all", "collective communication", "cuda", "triton", "tensor program", "training system", "llm system", "hbm")):
        return "systems"
    if "cs.CV" in categories or any(term in title.lower() for term in ("vision", "image", "visual", "video", "multimodal", "diffusion")):
        return "vision"
    return "text"


def discover(catalog, limit):
    """Harvest arXiv-deposited metadata through DataCite with bounded cursor recovery."""
    endpoint = "https://api.datacite.org/dois"
    allowed = ("cs.CL", "cs.LG", "cs.CV", "cs.RO", "cs.AI", "cs.DC", "cs.PF", "cs.AR", "cs.PL", "cs.OS")
    cursor = catalog.get("dataCiteDiscoveryCursor")
    if cursor:
        begin, end, page_cursor, offset = cursor["begin"], cursor["end"], cursor["cursor"], cursor["offset"]
    else:
        last = catalog.get("lastDataCiteDiscoveryAt") or catalog.get("lastDiscoveryAt")
        begin_date = (datetime.fromisoformat(last) if last else NOW - timedelta(days=4)) - timedelta(days=3)
        old_begin = (catalog.get("discoveryCursor") or {}).get("begin")
        if not catalog.get("lastDataCiteDiscoveryAt") and old_begin:
            begin_date = min(begin_date.date(), datetime.strptime(old_begin[:8], "%Y%m%d").date())
        begin, end, page_cursor, offset = begin_date.strftime("%Y-%m-%d"), NOW.date().isoformat(), "1", 0
    query = "created:[" + begin + " TO " + end + "] AND subjects.subject:(" + " OR ".join('"' + c + '"' for c in allowed) + ")"
    known = {paper["id"]: paper for paper in catalog["papers"]}
    processed = added = skipped = 0
    total = None
    provider_error = None
    rate_limited = complete = False
    latest = None
    print("Discovery: DataCite / arXiv registration window", begin, "to", end, "from offset", offset, flush=True)
    while processed < limit:
        params = {"provider-id": "arxiv", "query": query, "page[cursor]": page_cursor, "page[size]": 1000}
        try:
            result = json.loads(request_bytes(Request(endpoint + "?" + urlencode(params), headers=HEADERS)))
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("Unexpected DataCite result page")
            entries = result["data"]
            total = result.get("meta", {}).get("total")
            if type(total) is not int or total < 0:
                raise ValueError("DataCite omitted its result count")
            next_url = result.get("links", {}).get("next")
            next_cursor = None
            if next_url:
                parsed = urlparse(next_url)
                next_values = parse_qs(parsed.query).get("page[cursor]", [])
                if parsed.scheme != "https" or parsed.netloc != "api.datacite.org" or parsed.path != "/dois" or len(next_values) != 1 or not next_values[0] or next_values[0] == page_cursor:
                    raise ValueError("Invalid DataCite continuation cursor")
                next_cursor = next_values[0]
            if (not entries and (next_cursor or page_cursor == "1" and total)) or offset > len(entries):
                raise ValueError("DataCite returned an incomplete result page")
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError, TypeError, AttributeError) as error:
            provider_error = "DataCite discovery unavailable: " + str(error)
            rate_limited = isinstance(error, HTTPError) and error.code == 429
            break
        while offset < len(entries) and processed < limit:
            entry = entries[offset]
            offset += 1
            processed += 1
            try:
                attrs = entry["attributes"]
                doi = attrs["doi"]
                match = re.fullmatch(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", doi, re.IGNORECASE)
                if not match or entry.get("id", "").lower() != doi.lower():
                    raise ValueError("Invalid arXiv DOI")
                identity = match.group(1)
                source = urlparse(attrs["url"])
                if source.scheme not in ("http", "https") or source.netloc != "arxiv.org" or source.path != "/abs/" + identity or source.query or source.fragment:
                    raise ValueError("DOI and arXiv URL disagree")
                title = next((unescape(t["title"]) for t in attrs["titles"] if isinstance(t.get("title"), str) and t["title"].strip()), "")
                abstract = next((unescape(d["description"]) for d in attrs["descriptions"] if d.get("descriptionType") == "Abstract" and isinstance(d.get("description"), str)), "")
                title, abstract = " ".join(title.split()), " ".join(abstract.split())
                submitted = [d["date"] for d in attrs["dates"] if d.get("dateType") == "Submitted" and d.get("dateInformation") == "v1"]
                if len(submitted) != 1:
                    raise ValueError("Missing unambiguous first-submission date")
                published = datetime.fromisoformat(submitted[0].replace("Z", "+00:00")).date().isoformat()
                if published > NOW.date().isoformat():
                    raise ValueError("Future submission date")
                categories = []
                for subject in attrs["subjects"]:
                    category = re.search(r"\(([^()]+)\)$", subject.get("subject", ""))
                    if subject.get("subjectScheme") == "arXiv" and category:
                        categories.append(category.group(1))
                authors = []
                affiliations = set()
                for author in attrs["creators"]:
                    name = " ".join((author.get("givenName", "") + " " + author.get("familyName", "")).split()) or author.get("name", "")
                    if isinstance(name, str) and name.strip():
                        authors.append(" ".join(unescape(name).split()))
                    for affiliation in author.get("affiliation", []):
                        name = affiliation.get("name") if isinstance(affiliation, dict) else affiliation
                        if isinstance(name, str) and name.strip():
                            affiliations.add(" ".join(unescape(name).split()))
                if not title or not abstract or not authors or not any(c in allowed for c in categories):
                    raise ValueError("Incomplete or unrelated arXiv metadata")
            except (KeyError, TypeError, ValueError, AttributeError):
                skipped += 1
                continue
            relevant = any(c in categories for c in ("cs.CL", "cs.CV", "cs.RO"))
            relevant |= any(word in (title + " " + abstract).lower() for word in (
                "transformer", "language model", "neural network", "deep learning", "gpu",
                "attention", "foundation model", "mixture of experts", "mixture-of-experts",
                "reinforcement learning", "diffusion", "benchmark", "inference", "training",
                "cuda", "triton", "tensor compiler", "tensor program", "matrix multiplication"))
            if not relevant:
                skipped += 1
                continue
            latest = max(latest or published, published)
            paper = known.get(identity)
            if paper:
                provisional = paper.get("metadataSource") == "https://huggingface.co/api/daily_papers"
                paper.update({"abstract": abstract, "title": title, "authors": authors,
                              "metadataSource": endpoint + "/" + doi, "metadataUpdatedAt": STAMP})
                if provisional:
                    paper.update({"date": published, "dateBasis": "First arXiv submission (DataCite deposit)",
                                  "category": classify(title, abstract, categories)})
            else:
                paper = {
                    "id": identity, "title": title, "shortTitle": title,
                    "date": published, "dateBasis": "First arXiv submission (DataCite deposit)",
                    "category": classify(title, abstract, categories), "tags": ["new research"],
                    "family": None, "lab": None, "authors": authors,
                    "source": "https://arxiv.org/abs/" + identity, "abstract": abstract,
                    "summary": "", "why": "Newly discovered paper; editorial review is pending.",
                    "citationCount": None, "score": None, "curated": False, "protected": False,
                    "status": "candidate", "prerequisites": [], "discoveredAt": STAMP,
                    "metadataSource": endpoint + "/" + doi, "metadataUpdatedAt": STAMP,
                }
                catalog["papers"].append(paper)
                known[identity] = paper
                added += 1
            if affiliations:
                paper.update({"lab": " / ".join(sorted(affiliations)), "affiliationSource": endpoint + "/" + doi})
        if offset >= len(entries):
            if next_cursor is None:
                complete = True
                break
            page_cursor, offset = next_cursor, 0
    # Keep the old export-API cursor as provenance; this independent source replaces it.
    catalog["dataCiteDiscoveryCursor"] = None if complete else {"begin": begin, "end": end, "cursor": page_cursor, "offset": offset}
    if complete:
        catalog["lastDataCiteDiscoveryAt"] = end + "T00:00:00+00:00"
    catalog["discovery"] = {"source": "DataCite / arXiv", "scanned": processed, "added": added, "skipped": skipped,
                            "windowComplete": complete, "totalInWindow": total, "error": provider_error,
                            "rateLimited": rate_limited, "windowBegin": begin, "windowEnd": end,
                            "latestPublicationDate": latest, "nextOffset": None if complete else offset, "at": STAMP}
    if provider_error:
        print("::warning::" + provider_error + ". Successful pages retained; discovery resumes from its saved cursor.")
    print("Discovery: scanned", processed, "records, added", added, "candidates;", "window complete" if complete else "will resume", flush=True)


def discover_huggingface(catalog, limit=1000):
    """Discover community-selected papers without treating votes as citation evidence."""
    endpoint = "https://huggingface.co/api/daily_papers"
    cursor = catalog.get("huggingFaceDiscoveryCursor") or {
        "date": (NOW.date() - timedelta(days=6)).isoformat(), "end": NOW.date().isoformat(), "page": 0, "offset": 0}
    day, end, page, offset = cursor["date"], cursor["end"], cursor["page"], cursor.get("offset", 0)
    known = {p["id"]: p for p in catalog["papers"]}
    scanned = added = skipped = 0
    latest = catalog.get("huggingFaceDiscovery", {}).get("latestPublicationDate")
    error = None
    rate_limited = False
    while day <= end and scanned < limit:
        params = {"date": day, "sort": "publishedAt", "limit": 100, "p": page}
        try:
            rows = json.loads(request_bytes(Request(endpoint + "?" + urlencode(params), headers=HEADERS)))
            if not isinstance(rows, list):
                raise ValueError("Unexpected daily-paper response")
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError) as exc:
            error = "Hugging Face discovery unavailable: " + str(exc)
            rate_limited = isinstance(exc, HTTPError) and exc.code == 429
            break
        while offset < len(rows) and scanned < limit:
            row = rows[offset]
            offset += 1
            scanned += 1
            data = row.get("paper") if isinstance(row, dict) else None
            if not isinstance(data, dict):
                skipped += 1
                continue
            identity = re.sub(r"v\d+$", "", str(data.get("id", "")))
            title = " ".join(data["title"].split()) if isinstance(data.get("title"), str) else ""
            abstract = " ".join(data["summary"].split()) if isinstance(data.get("summary"), str) else ""
            authors = data.get("authors") if isinstance(data.get("authors"), list) else []
            published = str(data.get("publishedAt") or "")[:10]
            try:
                datetime.strptime(published, "%Y-%m-%d")
                if not re.fullmatch(r"\d{4}\.\d{4,5}", identity) or not title or not abstract:
                    raise ValueError("Incomplete paper metadata")
            except ValueError:
                skipped += 1
                continue
            latest = max(latest or published, published)
            paper = known.get(identity)
            if paper is None:
                paper = {"id": identity, "title": title, "shortTitle": title,
                         "date": published, "dateBasis": "Hugging Face publication date; awaiting arXiv verification",
                         "category": classify(title, abstract, []), "tags": ["new research"],
                         "family": None, "lab": None,
                         "authors": [a["name"] for a in authors if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip()],
                         "source": "https://arxiv.org/abs/" + identity, "abstract": abstract,
                         "summary": "", "why": "Newly discovered paper; editorial review is pending.",
                         "citationCount": None, "score": None, "curated": False, "protected": False,
                         "status": "candidate", "prerequisites": [], "discoveredAt": STAMP,
                         "metadataSource": endpoint, "metadataUpdatedAt": STAMP}
                catalog["papers"].append(paper)
                known[identity] = paper
                added += 1
            previous = paper.get("huggingFace", {})
            votes = data.get("upvotes")
            featured = data.get("submittedOnDailyAt")
            paper["huggingFace"] = {"url": "https://huggingface.co/papers/" + identity,
                                    "featuredAt": featured if isinstance(featured, str) and featured else day,
                                    "upvotes": votes if type(votes) is int and votes >= 0 else previous.get("upvotes"),
                                    "updatedAt": STAMP}
        if offset >= len(rows):
            if len(rows) < 100:
                day = (datetime.strptime(day, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
                page = 0
            else:
                page += 1
            offset = 0
    complete = day > end and error is None
    catalog["huggingFaceDiscoveryCursor"] = None if complete else {"date": day, "end": end, "page": page, "offset": offset}
    catalog["huggingFaceDiscovery"] = {"source": "Hugging Face Daily Papers", "scanned": scanned, "added": added,
                                        "skipped": skipped, "windowComplete": complete, "windowEnd": end,
                                        "latestPublicationDate": latest, "error": error, "rateLimited": rate_limited,
                                        "cursor": catalog["huggingFaceDiscoveryCursor"], "at": STAMP}
    if error:
        print("::warning::" + error + ". Saved papers retained; the next run resumes this feed.")
    print("Hugging Face discovery:", scanned, "scanned,", added, "added,", skipped, "skipped;", "window complete" if complete else "will resume", flush=True)


def refresh_citations(catalog, limit=1000):
    available = [p for p in catalog["papers"] if p.get("semanticScholarId") or re.fullmatch(r"\d{4}\.\d{4,5}", p["id"])]
    papers = []
    for paper in available:
        missing = paper.get("citationCount") is None or not paper.get("semanticScholarId") or not paper.get("referenceEvidenceComplete")
        stale = (NOW - timedelta(days=7 if paper.get("status") == "active" else 30)).isoformat()
        if (missing or paper.get("citationUpdatedAt", "") < stale) and paper.get("citationCheckedAt", "") < (NOW - timedelta(days=1)).isoformat():
            papers.append(paper)
    # Give new papers missing lineage an early turn without starving historical refreshes.
    pending, background = [], []
    for paper in papers:
        recent = (NOW.date() - datetime.strptime(paper["date"], "%Y-%m-%d").date()).days < catalog["policy"]["recentMonths"] * 30.4375
        (pending if recent and (not paper.get("semanticScholarId") or not paper.get("referenceEvidenceComplete")) else background).append(paper)
    pending.sort(key=lambda p: (p.get("citationCheckedAt", ""), not bool(p.get("huggingFace")), -(consequentiality(p, catalog["policy"], catalog.get("authorMetrics", {}))["score"] or 0), -datetime.strptime(p["date"], "%Y-%m-%d").toordinal()))
    background.sort(key=lambda p: (p.get("citationCheckedAt", ""), -(p.get("citationCount") or 0)))
    papers = []
    for start in range(0, max(len(pending), len(background) * 2), 100):
        papers.extend(pending[start:start + 100])
        papers.extend(background[start // 2:start // 2 + 50])
    for paper in papers:
        paper["citationFresh"] = False
    headers = {**HEADERS, "Content-Type": "application/json"}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    fields = "externalIds,citationCount,url,authors"
    matched = checked = 0
    rate_limited = False
    provider_error = None
    for start in range(0, min(len(papers), limit), 50):
        batch = papers[start:min(start + 50, limit)]
        ids = [p.get("semanticScholarId") or "ARXIV:" + p["id"] for p in batch]
        payload = json.dumps({"ids": ids}).encode()
        try:
            batch_fields = fields + (",references.externalIds" if any(not p.get("referenceEvidenceComplete") for p in batch) else "")
            results = json.loads(request_bytes(Request(S2 + "?" + urlencode({"fields": batch_fields}), data=payload, headers=headers)))
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
            paper["authorIdentitiesCheckedAt"] = STAMP
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
                          "citationSourceUrl": result.get("url"), "semanticScholarSourceUrl": result.get("url"), "citationFresh": True})
            if ext.get("DOI"):
                paper["doi"] = ext["DOI"]
            if isinstance(result.get("authors"), list):
                paper["authorIdentitiesCheckedAt"] = STAMP
                authors = [a for a in result["authors"] if isinstance(a, dict) and a.get("authorId") and a.get("name")]
                if authors:
                    paper["semanticScholarAuthors"] = authors
                    paper["authorIdentitiesUpdatedAt"] = STAMP
            if isinstance(result.get("references"), list) and result["references"]:
                references = [r for r in result["references"] if isinstance(r, dict)]
                paper["referencedPaperIds"] = [r["paperId"] for r in references if r.get("paperId")]
                paper["referencedArxivIds"] = [r["externalIds"]["ArXiv"] for r in references if isinstance(r.get("externalIds"), dict) and r["externalIds"].get("ArXiv")]
                paper["referenceEvidenceComplete"] = True
            matched += 1
        print("Citation progress:", checked, "checked of", len(papers), flush=True)
    if provider_error:
        print("::warning::" + provider_error + ". Prior evidence retained; the next run starts with unchecked papers.")
    catalog["refresh"] = {"provider": "Semantic Scholar", "matched": matched, "checked": checked, "requested": len(papers),
                          "totalEligible": len(available), "notDue": len(available) - len(papers),
                          "budget": limit, "complete": matched == len(papers), "rateLimited": rate_limited,
                          "error": provider_error, "remaining": len(papers) - checked, "at": STAMP}
    print("Citation refresh:", matched, "matched of", len(papers), "requested; unmatched records retain prior evidence.")


def refresh_author_reputation(catalog, paper_limit=500, profile_limit=5000):
    """Refresh publication-linked author profiles weekly; retain partial evidence."""
    recent = [p for p in catalog["papers"] if (p.get("semanticScholarId") or re.fullmatch(r"\d{4}\.\d{4,5}", p["id"])) and
              (NOW.date() - datetime.strptime(p["date"], "%Y-%m-%d").date()).days < catalog["policy"]["recentMonths"] * 30.4375]
    stale = (NOW - timedelta(days=7)).isoformat()
    headers = {**HEADERS, "Content-Type": "application/json"}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    missing = [p for p in recent if p.get("authorIdentitiesCheckedAt", "") < (NOW - timedelta(days=1)).isoformat()
               and (not p.get("semanticScholarAuthors") or p.get("authorIdentitiesUpdatedAt", "") < stale)]
    cache = catalog.setdefault("authorMetrics", {})
    missing.sort(key=lambda p: (p.get("authorIdentitiesCheckedAt", ""), not bool(p.get("huggingFace")), -(consequentiality(p, catalog["policy"], cache)["score"] or 0), -datetime.strptime(p["date"], "%Y-%m-%d").toordinal()))
    matched = profiles_checked = 0
    identities_checked = identities_matched = 0
    error = None
    for start in range(0, min(len(missing), paper_limit), 100):
        batch = missing[start:min(start + 100, paper_limit)]
        ids = [p.get("semanticScholarId") or "ARXIV:" + p["id"] for p in batch]
        try:
            rows = json.loads(request_bytes(Request(S2 + "?fields=externalIds,authors", data=json.dumps({"ids": ids}).encode(), headers=headers)))
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ValueError("Unexpected paper-author response")
            for paper, row in zip(batch, rows):
                paper["authorIdentitiesCheckedAt"] = STAMP
                identities_checked += 1
                if not isinstance(row, dict) or not row.get("paperId"):
                    continue
                ext = row.get("externalIds") if isinstance(row.get("externalIds"), dict) else {}
                arxiv = ext.get("ArXiv")
                if (arxiv and arxiv != paper["id"]) or not (arxiv == paper["id"] or paper.get("semanticScholarId") == row["paperId"]):
                    continue
                paper["semanticScholarId"] = row["paperId"]
                if isinstance(row.get("authors"), list):
                    authors = [a for a in row["authors"] if isinstance(a, dict) and a.get("authorId") and a.get("name")]
                    if authors:
                        paper["semanticScholarAuthors"] = authors
                        paper["authorIdentitiesUpdatedAt"] = STAMP
                        identities_matched += 1
            print("Author identity progress:", identities_checked, "of", len(missing), flush=True)
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError) as exc:
            error = str(exc)
            break
    profile_checks = catalog.setdefault("authorProfileChecks", {})
    ids = sorted({a["authorId"] for p in recent for a in p.get("semanticScholarAuthors", [])
                  if cache.get(a["authorId"], {}).get("updatedAt", "") < stale
                  and profile_checks.get(a["authorId"], "") < (NOW - timedelta(days=1)).isoformat()})
    featured_authors = {a["authorId"] for p in recent if p.get("huggingFace") for a in p.get("semanticScholarAuthors", [])}
    ids.sort(key=lambda identity: (profile_checks.get(identity, cache.get(identity, {}).get("updatedAt", "")), identity not in featured_authors, identity))
    # Author profiles are looked up by IDs attached to these publications, never by a name search.
    for start in range(0, min(len(ids), profile_limit), 500):
        batch = ids[start:min(start + 500, profile_limit)]
        try:
            url = "https://api.semanticscholar.org/graph/v1/author/batch?fields=name,hIndex,citationCount,paperCount,url"
            rows = json.loads(request_bytes(Request(url, data=json.dumps({"ids": batch}).encode(), headers=headers)))
            if not isinstance(rows, list):
                raise ValueError("Unexpected author-profile response")
            for identity in batch:
                profile_checks[identity] = STAMP
            profiles_checked += len(batch)
            for author in rows:
                if not isinstance(author, dict) or author.get("authorId") not in batch or not isinstance(author.get("hIndex"), int) or author["hIndex"] < 0:
                    continue
                cache[author["authorId"]] = {**author, "updatedAt": STAMP}
                matched += 1
            print("Author profile progress:", min(start + 500, len(ids), profile_limit), "of", len(ids), flush=True)
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError) as exc:
            error = str(exc)
            break
    catalog["authorRefresh"] = {"at": STAMP, "requestedPapers": len(missing), "checkedPapers": identities_checked,
                               "matchedPapers": identities_matched, "requestedProfiles": len(ids), "checkedProfiles": profiles_checked, "matchedProfiles": matched,
                               "paperBudget": paper_limit, "profileBudget": profile_limit, "cachedProfiles": len(cache), "error": error}
    if error:
        print("::warning::Author evidence refresh incomplete; saved profiles retained:", error)


def refresh_openalex(catalog, limit=400):
    """Supplement citation evidence through exact, title-verified arXiv matches."""
    stale = (NOW - timedelta(days=7)).isoformat()
    papers = [p for p in catalog["papers"] if re.fullmatch(r"\d{4}\.\d{4,5}", p["id"])
              and p.get("openAlexCheckedAt", "")[:10] != STAMP[:10]
              and p.get("openAlex", {}).get("updatedAt", "") < stale]
    papers.sort(key=lambda p: (p.get("openAlexCheckedAt", ""), p.get("selectionPolicy") != "learning-anchor" and not p.get("protected"), not bool(p.get("huggingFace")),
                              bool(p.get("referenceEvidenceComplete")), p.get("citationCount") is not None,
                              -datetime.strptime(p["date"], "%Y-%m-%d").toordinal()))
    headers = dict(HEADERS)
    if os.environ.get("OPENALEX_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENALEX_API_KEY"]
    checked = matched = ambiguous = rejected = 0
    errors = []
    rate_limited = False
    for start in range(0, min(len(papers), limit), 30):
        batch = papers[start:min(start + 30, limit)]
        by_id = {p["id"]: p for p in batch}
        urls = [url for p in batch for url in ("http://arxiv.org/abs/" + p["id"],
                "https://arxiv.org/abs/" + p["id"], "https://doi.org/10.48550/arxiv." + p["id"])]
        params = {"filter": "locations.landing_page_url:" + "|".join(urls), "per_page": 100,
                  "select": "id,doi,title,cited_by_count,referenced_works,locations"}
        for paper in batch:
            paper["openAlexCheckedAt"] = STAMP
        checked += len(batch)
        try:
            payload = json.loads(request_bytes(Request("https://api.openalex.org/works?" + urlencode(params), headers=headers)))
            rows = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Unexpected OpenAlex work response")
            meta = payload.get("meta")
            total = meta.get("count") if isinstance(meta, dict) else None
            if type(total) is not int or total != len(rows):
                raise ValueError("Incomplete OpenAlex batch; ambiguous matches cannot be ruled out")
        except (HTTPError, URLError, TimeoutError, ConnectionError, ValueError) as error:
            rate_limited = isinstance(error, HTTPError) and error.code == 429
            errors.append("OpenAlex unavailable: " + str(error))
            break
        matches = {p["id"]: {} for p in batch}
        for row in rows:
            locations = row.get("locations")
            if not isinstance(locations, list):
                rejected += 1
                continue
            identities = set()
            for url in [row.get("doi")] + [loc.get("landing_page_url") for loc in locations if isinstance(loc, dict)]:
                if not isinstance(url, str):
                    continue
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                path = parsed.path.casefold().rstrip("/")
                match = None
                if parsed.hostname in ("arxiv.org", "export.arxiv.org"):
                    match = re.fullmatch(r"/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", path)
                elif parsed.hostname in ("doi.org", "dx.doi.org"):
                    match = re.fullmatch(r"/10\.48550/arxiv\.(\d{4}\.\d{4,5})(?:v\d+)?", path)
                if match:
                    identities.add(match[1])
            identity = next(iter(identities)) if len(identities) == 1 else None
            paper = by_id.get(identity)
            title = row.get("title")
            count = row.get("cited_by_count")
            work_id = row.get("id")
            refs = row.get("referenced_works")
            if (not paper or not isinstance(title, str) or not isinstance(work_id, str)
                    or not re.fullmatch(r"https://openalex\.org/W\d+", work_id)
                    or re.sub(r"[\W_]+", "", title.casefold()) != re.sub(r"[\W_]+", "", paper["title"].casefold())
                    or type(count) is not int or count < 0 or not isinstance(refs, list)
                    or any(not isinstance(ref, str) or not re.fullmatch(r"https://openalex\.org/W\d+", ref) for ref in refs)):
                rejected += 1
                continue
            matches[identity][work_id] = row
        for paper in batch:
            candidates = matches[paper["id"]]
            if len(candidates) > 1:
                ambiguous += 1
                continue
            if not candidates:
                continue
            row = next(iter(candidates.values()))
            prior = paper.get("openAlex", {})
            references = row["referenced_works"] or (prior.get("referencedWorkIds", []) if prior.get("id") == row["id"] else [])
            paper["openAlex"] = {"id": row["id"], "citationCount": row["cited_by_count"],
                                "referencedWorkIds": sorted(set(references)), "updatedAt": STAMP,
                                "sourceUrl": row["id"]}
            if paper.get("citationCount") is None or paper.get("citationProvider") == "OpenAlex":
                paper.update({"citationCount": row["cited_by_count"], "citationProvider": "OpenAlex",
                              "citationUpdatedAt": STAMP, "citationSourceUrl": row["id"], "citationFresh": True})
            matched += 1
        print("OpenAlex progress:", checked, "checked,", matched, "matched,", ambiguous, "ambiguous,", rejected, "rejected", flush=True)
    catalog["openAlexRefresh"] = {"provider": "OpenAlex", "at": STAMP, "requested": len(papers), "checked": checked,
                                 "matched": matched, "ambiguous": ambiguous, "rejected": rejected, "budget": limit,
                                 "remaining": len(papers) - checked, "rateLimited": rate_limited, "errors": errors}
    if errors:
        print("::warning::" + errors[-1] + ". Existing citation evidence retained.")


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
    by_openalex = {}
    for paper in papers:
        identity = paper.get("openAlex", {}).get("id")
        if identity:
            by_openalex[identity] = paper["id"] if identity not in by_openalex else None
    edges = {}
    for paper in papers:
        refs = set(paper.get("referencedArxivIds", []))
        refs.update(by_s2[r] for r in paper.get("referencedPaperIds", []) if r in by_s2)
        for ref in sorted(refs):
            if ref in by_arxiv and ref != paper["id"] and by_arxiv[ref]["date"] <= paper["date"]:
                source_url = paper.get("semanticScholarSourceUrl") or ("https://www.semanticscholar.org/paper/" + paper["semanticScholarId"] if paper.get("semanticScholarId") else paper.get("citationSourceUrl"))
                edge = {"source": ref, "target": paper["id"], "type": "citation", "provider": "Semantic Scholar", "sourceUrl": source_url}
                edges[(ref, paper["id"], "citation")] = edge
        for identity in paper.get("openAlex", {}).get("referencedWorkIds", []):
            ref = by_openalex.get(identity)
            if ref and ref != paper["id"] and by_arxiv[ref]["date"] <= paper["date"]:
                edges.setdefault((ref, paper["id"], "citation"), {"source": ref, "target": paper["id"], "type": "citation",
                                 "provider": "OpenAlex", "sourceUrl": paper["openAlex"]["sourceUrl"]})
        for prerequisite in paper.get("prerequisites", []):
            identity = prerequisite if isinstance(prerequisite, str) else prerequisite["id"]
            if identity in by_arxiv and identity != paper["id"]:
                edges[(identity, paper["id"], "prerequisite")] = {"source": identity, "target": paper["id"], "type": "prerequisite", "reason": prerequisite.get("reason", "Curated prerequisite") if isinstance(prerequisite, dict) else "Curated prerequisite"}
    previous = {p["id"]: (p.get("score"), p.get("status", "candidate")) for p in papers}
    policy = catalog["policy"]
    fixed = {p["id"] for p in papers if p.get("protected")} | set(policy["learningAnchors"])
    remaining = list(fixed)
    while remaining:
        for prerequisite in by_arxiv[remaining.pop()].get("prerequisites", []):
            identity = prerequisite if isinstance(prerequisite, str) else prerequisite["id"]
            if identity in by_arxiv and identity not in fixed:
                fixed.add(identity)
                remaining.append(identity)
    for paper in papers:
        paper.update(consequentiality(paper, policy, catalog.get("authorMetrics", {})))
        paper["selectionCohort"] = "recent" if paper["scoreComponents"]["ageMonths"] < policy["recentMonths"] else "mature"
        paper["selectionPolicy"] = "learning-anchor" if paper["id"] in fixed else "cohort-score"
    factors = policy["categoryFactors"]
    optional = [p for p in papers if p["id"] not in fixed and p["score"] is not None and not p.get("outOfScopeReason")]
    guides = {"mature": policy["targetVisible"] - policy["recentTarget"], "recent": policy["recentTarget"]}
    bases = {cohort: policy.get("cohortBaseCutoffs", {}).get(cohort, policy["minimumCutoff"]) for cohort in guides}
    potential = fixed | {p["id"] for p in optional}
    connected_pool = {endpoint for e in edges.values() if e["source"] in potential and e["target"] in potential
                      for endpoint in (e["source"], e["target"])}
    monthly = {}
    for paper in optional:
        if paper["id"] in connected_pool:
            monthly.setdefault(paper["date"][:7], []).append(paper["score"] / factors[paper["category"]])
    density_bases = {}
    for month, levels in monthly.items():
        slots = max(0, policy["monthlyVisibleLimit"] - sum(by_arxiv[identity]["date"].startswith(month) for identity in fixed))
        if len(levels) > slots:
            # Raise the category-adjusted boundary above the first excluded score;
            # equal scores stay together instead of overflowing a crowded month.
            density_bases[month] = round(sorted(levels, reverse=True)[slots] + .0001, 4)

    def admission_threshold(paper, cutoffs):
        factor = factors[paper["category"]]
        return round(max(min(100, cutoffs[paper["selectionCohort"]] * factor),
                         density_bases.get(paper["date"][:7], 0) * factor), 6)

    def admitted(cutoffs):
        eligible = fixed | {p["id"] for p in optional if p["score"] >= admission_threshold(p, cutoffs)}
        connected = {endpoint for e in edges.values() if e["source"] in eligible and e["target"] in eligible
                     for endpoint in (e["source"], e["target"])}
        return fixed | (eligible & connected)

    # Recent reputation and mature citation signals get separate admission boundaries.
    # Keep boundaries steady inside the count bands, allowing membership to grow and shrink.
    fresh = set(policy.get("cohortBaseCutoffs", {})) != set(guides)
    for _ in range(2):
        for cohort, guide in guides.items():
            members = {p["id"] for p in papers if p["selectionCohort"] == cohort}
            count = len(admitted(bases) & members)
            if not fresh and round(guide * (1 - policy["countTolerance"])) <= count <= round(guide * (1 + policy["countTolerance"])):
                continue
            levels = sorted({policy["minimumCutoff"], 100.0} | {
                round(max(policy["minimumCutoff"], min(100, p["score"] / factors[p["category"]])), 6)
                for p in optional if p["selectionCohort"] == cohort})
            low, high, best = 0, len(levels) - 1, None
            while low <= high:
                mid = (low + high) // 2
                base = levels[mid]
                count = len(admitted({**bases, cohort: base}) & members)
                quality = (abs(count - guide), count > guide, -base)
                if best is None or quality < best[0]:
                    best = (quality, base)
                if count > guide:
                    low = mid + 1
                else:
                    high = mid - 1
            bases[cohort] = best[1]
        fresh = False
    visible = admitted(bases)
    policy["cohortBaseCutoffs"] = bases
    cohort_cutoffs = {cohort: {category: round(min(100, base * factor), 6) for category, factor in factors.items()} for cohort, base in bases.items()}
    cutoff = bases["recent"]
    policy["cutoff"] = cutoff
    policy["categoryCutoffs"] = cohort_cutoffs["recent"]
    for paper in papers:
        paper["status"] = "active" if paper["id"] in visible else "archived"
        if paper["id"] in fixed:
            paper["selectionBasis"] = "foundation" if paper.get("protected") else "learning-anchor"
            paper["selectionReason"] = "Learning-path anchor or prerequisite"
            paper.pop("selectionThreshold", None)
            continue
        if paper.get("outOfScopeReason"):
            paper["selectionBasis"] = "out-of-scope"
            paper["selectionReason"] = "Outside map scope: " + paper["outOfScopeReason"]
            paper.pop("selectionThreshold", None)
            continue
        threshold = admission_threshold(paper, bases)
        meets_score = paper["score"] is not None and paper["score"] >= threshold
        cohort_threshold = cohort_cutoffs[paper["selectionCohort"]][paper["category"]]
        crowded = threshold > cohort_threshold and paper["score"] is not None and paper["score"] >= cohort_threshold
        paper["selectionThreshold"] = threshold
        paper["selectionBasis"] = "score" if meets_score else "pending"
        paper["selectionReason"] = ("No connection to the visible graph yet" if meets_score and paper["id"] not in visible
                                    else "Meets category, age-cohort, and monthly density cutoffs" if meets_score
                                    else "Awaiting qualifying citation or reputation evidence" if paper["score"] is None
                                    else "Below crowded-month category cutoff" if crowded else "Below category cutoff")
    catalog["selectionCalibration"] = {"at": STAMP, "targetVisible": policy["targetVisible"], "visibleCount": len(visible),
                                       "baseCutoff": cutoff, "categoryCutoffs": policy["categoryCutoffs"], "anchorCount": len(fixed),
                                       "cohorts": {cohort: {"guide": guide, "visibleCount": sum(p["id"] in visible and p["selectionCohort"] == cohort for p in papers),
                                                             "baseCutoff": bases[cohort], "categoryCutoffs": cohort_cutoffs[cohort],
                                                             "lowerGuide": round(guide * (1 - policy["countTolerance"])), "upperGuide": round(guide * (1 + policy["countTolerance"]))}
                                                   for cohort, guide in guides.items()},
                                       "monthlyDensity": {"limit": policy["monthlyVisibleLimit"],
                                                          "months": {month: {"baseCutoff": base,
                                                                             "categoryCutoffs": {category: round(base * factor, 6) for category, factor in factors.items()},
                                                                             "visibleCount": sum(p["id"] in visible and p["date"].startswith(month) for p in papers)}
                                                                     for month, base in sorted(density_bases.items())}},
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
        "Category cutoffs are calibrated separately for mature and recent papers around a " + str(policy["targetVisible"]) + "-paper guide, staying stable within the configured count bands. "
        "Crowded months raise category cutoffs to keep at most " + str(policy["monthlyVisibleLimit"]) + " visible papers, with learning anchors retained. "
        "Only learning anchors and their prerequisites are reserved; all other papers need qualifying evidence and an actual graph connection. Hugging Face features prioritize enrichment, not score.")
    catalog["papers"].sort(key=lambda p: (p["date"], p["id"]))
    details = {}
    index = []
    for paper in catalog["papers"]:
        year = paper["date"][:4]
        details.setdefault(year, {})[paper["id"]] = {"abstract": paper.get("abstract"), "metadataSource": paper.get("metadataSource"), "dateBasis": paper.get("dateBasis"), "affiliationSource": paper.get("affiliationSource")}
        drop = {"abstract", "referencedPaperIds", "referencedArxivIds", "semanticScholarAliases", "metadataSource", "affiliationSource", "releaseDateSource", "referenceEvidenceComplete", "semanticScholarAuthors", "authorIdentitiesUpdatedAt", "authorIdentitiesCheckedAt"}
        record = {key: value for key, value in paper.items() if key not in drop}
        if "openAlex" in record:
            record["openAlex"] = {key: value for key, value in record["openAlex"].items() if key != "referencedWorkIds"}
        record["detailFile"] = "./data/details/" + year + ".json"
        index.append(record)
    (DATA / "details").mkdir(exist_ok=True)
    for year, records in details.items():
        (DATA / "details" / (year + ".json")).write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n")
    latest = [p for p in papers if p["date"] >= (NOW - timedelta(days=14)).date().isoformat()]
    reasons = {p.get("selectionReason", "Pending evidence") for p in latest if p["status"] == "archived"}
    catalog["pipelineStatus"] = {"trackedCount": len(papers), "visibleCount": len(visible),
                                 "latestTrackedDate": max((p["date"] for p in papers), default=None),
                                 "latestVisibleDate": max((p["date"] for p in papers if p["id"] in visible), default=None),
                                 "recentWindowDays": 14, "recentTrackedCount": len(latest),
                                 "recentVisibleCount": sum(p["id"] in visible for p in latest),
                                 "recentMissingReputation": sum(p["scoreComponents"]["reputationSignal"] is None for p in latest),
                                 "recentMissingReferences": sum(not p.get("referenceEvidenceComplete") for p in latest),
                                 "recentExclusions": {reason: sum(p["status"] == "archived" and p.get("selectionReason", "Pending evidence") == reason for p in latest) for reason in sorted(reasons)},
                                 "entered": sum(p["status"] == "active" and previous[p["id"]][1] != "active" for p in papers),
                                 "exited": sum(p["status"] == "archived" and previous[p["id"]][1] == "active" for p in papers)}
    export = {"updatedAt": STAMP, "papers": index, "links": sorted(edges.values(), key=lambda e: (e["source"], e["target"], e["type"])),
              "scoreDescription": catalog["scoreDescription"], "refresh": catalog.get("refresh"),
              "discovery": catalog.get("discovery"), "huggingFaceDiscovery": catalog.get("huggingFaceDiscovery"),
              "openAlexRefresh": catalog.get("openAlexRefresh"), "requestState": catalog.get("requestState"), "cutoff": cutoff, "policyVersion": policy["version"],
              "recentMonths": policy["recentMonths"], "categoryCutoffs": policy.get("categoryCutoffs", {}),
              "visibleCount": sum(p["status"] != "archived" for p in papers), "selectionCalibration": catalog["selectionCalibration"],
              "pipelineStatus": catalog["pipelineStatus"]}
    (DATA / "papers.json").write_text(json.dumps(export, ensure_ascii=False, separators=(",", ":")) + "\n")
    if changes:
        with (DATA / "history.jsonl").open("a") as history:
            history.write(json.dumps({"at": STAMP, "policyVersion": catalog["policy"]["version"], "changes": changes}, separators=(",", ":")) + "\n")
    print("Exported", len(papers), "papers,", len(edges), "links,", len(changes), "score or membership changes.")
    print("Pipeline:", json.dumps(catalog["pipelineStatus"], sort_keys=True))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write("## Research atlas update\n\n| Stage | Result |\n| --- | --- |\n")
            for label, result in catalog["pipelineStatus"].items():
                summary.write("| " + label + " | " + str(result) + " |\n")
            for stage in ("discovery", "huggingFaceDiscovery", "authorRefresh", "refresh", "openAlexRefresh", "requestState"):
                summary.write("\n**" + stage + "**: `" + json.dumps(catalog.get(stage, {})) + "`\n")


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
    global REQUEST_STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true", help="Discover arXiv deposits through DataCite and Hugging Face.")
    parser.add_argument("--refresh", action="store_true", help="Refresh due evidence from Semantic Scholar and OpenAlex.")
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
    REQUEST_STATE = catalog.setdefault("requestState", {})
    for state in REQUEST_STATE.values():
        state.update({"requestsThisRun": 0, "deferredThisRun": 0})
    try:
        if args.discover:
            discover(catalog, args.limit)
            discover_huggingface(catalog)
        if args.refresh:
            refresh_citations(catalog)
            refresh_author_reputation(catalog)
            refresh_openalex(catalog)
        validate(catalog)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
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
