"""Scrape validated ATS boards directly, filter to Ireland, tag, and merge.

Design (mirrors the RoleMap Ireland GMP Pipeline):
- source of truth is each employer's own ATS API (Workday CXS,
  SmartRecruiters, Greenhouse), never a job board's aggregation
- Ireland-only: a role is kept only when its location resolves to Ireland
- multi-label tagging into 5 categories + a strong-CV-fit flag
- expire, don't delete: a posting that disappears is stamped inactive with
  the date, not erased
- a 200 with a malformed body is retried, never trusted (see common.http_json)
"""
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import sys

from common import (http_json, looks_irish, county_of, tag, fit_flag)

HERE = pathlib.Path(__file__).parent
REG_PATH = HERE / "companies.json"
OUT_PATH = HERE.parent / "docs" / "data" / "jobs.json"
TODAY = dt.date.today().isoformat()

SECTOR = {"West Pharmaceutical Services": "MedTech"}
PAGE = 20
MAX_DETAIL_WORKERS = 6


def _strip_html(html):
    return re.sub(r"<[^>]+>", " ", html or "")


# ---------------------------------------------------------------- Workday ---

def workday_ireland_facet(host, tenant, site):
    """Resolve this tenant's Ireland locationCountry facet id dynamically."""
    resp = http_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                     body={"appliedFacets": {}, "limit": 1, "offset": 0})
    for facet in resp.get("facets", []):
        if facet.get("facetParameter") == "locationCountry":
            for v in facet.get("values", []):
                if v.get("descriptor") == "Ireland":
                    return v.get("id"), resp.get("total", 0)
    return None, resp.get("total", 0)


def workday_list(host, tenant, site, body_extra):
    """Paginate one filtered listing; cross-page zero-consistency check."""
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out, offset, expected = [], 0, None
    while True:
        body = {"appliedFacets": {}, "limit": PAGE, "offset": offset}
        body.update(body_extra)
        resp = http_json(url, body=body)
        if "total" not in resp or "jobPostings" not in resp:
            raise RuntimeError(f"malformed page at offset {offset} for {tenant}/{site}")
        total = resp["total"]
        if expected is None:
            expected = total
        elif total == 0 and out:
            # soft throttle lying that the board is empty (DEV-02): retry once
            resp = http_json(url, body=body, retries=4, backoff=5.0)
            total = resp.get("total", 0)
            if total == 0:
                break
        out.extend(resp["jobPostings"])
        offset += PAGE
        if offset >= (total or 0) or not resp["jobPostings"]:
            break
    return out


def workday_detail(host, tenant, site, external_path):
    resp = http_json(f"https://{host}/wday/cxs/{tenant}/{site}{external_path}")
    info = resp.get("jobPostingInfo")
    if not info:  # 200 with an empty detail body (DEV-03): one long retry
        resp = http_json(f"https://{host}/wday/cxs/{tenant}/{site}{external_path}",
                         retries=4, backoff=5.0)
        info = resp.get("jobPostingInfo") or {}
    return info


def scrape_workday(entry):
    host, tenant, site = entry["host"], entry["tenant"], entry["board"]
    facet_id, _ = workday_ireland_facet(host, tenant, site)
    if facet_id:
        postings = workday_list(host, tenant, site,
                                {"appliedFacets": {"locationCountry": [facet_id]}})
    else:
        # no country facet exposed: union of location searches
        seen, postings = set(), []
        for term in ("Ireland", "Dublin", "Cork", "Limerick", "Athlone",
                     "Waterford", "Sligo", "Dundalk", "Westport", "Galway"):
            for p in workday_list(host, tenant, site, {"searchText": term}):
                if p.get("externalPath") not in seen:
                    seen.add(p.get("externalPath"))
                    postings.append(p)

    jobs = []

    def hydrate(p):
        path = p.get("externalPath")
        if not path:
            return None
        loc_text = p.get("locationsText", "")
        info = workday_detail(host, tenant, site, path)
        country = (info.get("country") or {}).get("descriptor", "")
        loc = info.get("location") or loc_text
        if country != "Ireland" and not looks_irish(f"{loc} {loc_text}"):
            return None
        title = info.get("title") or p.get("title", "")
        cats = tag(title, _strip_html(info.get("jobDescription", "")))
        if not cats:
            return None
        posted = (info.get("startDate") or "")[:10] or None
        return {
            "co": entry["name"], "t": title, "loc": loc,
            "cty": county_of(f"{loc} {loc_text}") or "Dublin",
            "d": posted,
            "u": f"https://{host}/en-US/{site}{path}",
            "c": cats, "fit": fit_flag(title),
            "s": SECTOR.get(entry["name"], "Pharma & Biologics"),
            "id": f"workday|{tenant}|{path}",
        }

    with cf.ThreadPoolExecutor(MAX_DETAIL_WORKERS) as pool:
        for job in pool.map(hydrate, postings):
            if job:
                jobs.append(job)
    return jobs


# --------------------------------------------------------- SmartRecruiters ---

def scrape_smartrecruiters(entry):
    cid, jobs, offset = entry["board"], [], 0
    while True:
        resp = http_json("https://api.smartrecruiters.com/v1/companies/"
                         f"{cid}/postings?country=ie&limit=100&offset={offset}")
        content = resp.get("content", [])
        for p in content:
            loc = p.get("location") or {}
            loc_text = ", ".join(filter(None, [loc.get("city"), loc.get("region")]))
            if loc.get("country", "").lower() not in ("ie", "ireland"):
                continue
            title = p.get("name", "")
            cats = tag(title)
            if not cats:
                continue
            jobs.append({
                "co": entry["name"], "t": title, "loc": loc.get("city") or "Ireland",
                "cty": county_of(loc_text) or county_of(loc.get("city", "")) or "Dublin",
                "d": (p.get("releasedDate") or "")[:10] or None,
                "u": f"https://jobs.smartrecruiters.com/{cid}/{p.get('id')}",
                "c": cats, "fit": fit_flag(title),
                "s": SECTOR.get(entry["name"], "Pharma & Biologics"),
                "id": f"smartrecruiters|{cid}|{p.get('id')}",
            })
        offset += 100
        if offset >= resp.get("totalFound", 0) or not content:
            break
    return jobs


# ---------------------------------------------------------------- Greenhouse -

def scrape_greenhouse(entry):
    token = entry["board"]
    resp = http_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    jobs = []
    for p in resp.get("jobs", []):
        loc = (p.get("location") or {}).get("name", "")
        if not looks_irish(loc):
            continue
        title = p.get("title", "")
        cats = tag(title, _strip_html(p.get("content", "")))
        if not cats:
            continue
        jobs.append({
            "co": entry["name"], "t": title, "loc": loc,
            "cty": county_of(loc) or "Dublin",
            "d": (p.get("updated_at") or "")[:10] or None,
            "u": p.get("absolute_url", ""),
            "c": cats, "fit": fit_flag(title),
            "s": SECTOR.get(entry["name"], "Pharma & Biologics"),
            "id": f"greenhouse|{token}|{p.get('id')}",
        })
    return jobs


SCRAPERS = {
    "workday": scrape_workday,
    "smartrecruiters": scrape_smartrecruiters,
    "greenhouse": scrape_greenhouse,
}


# ------------------------------------------------------------------- merge ---

def merge(harvest):
    prev = {}
    if OUT_PATH.exists():
        try:
            for j in json.loads(OUT_PATH.read_text()).get("jobs", []):
                prev[j["id"]] = j
        except (ValueError, KeyError):
            pass
    now = {j["id"]: j for j in harvest}

    merged = []
    for jid, job in now.items():
        old = prev.get(jid, {})
        job["first_seen"] = old.get("first_seen", TODAY)
        job["last_seen"] = TODAY
        job["active"] = True
        if not job["d"]:
            job["d"] = old.get("d") or job["first_seen"]
        merged.append(job)
    for jid, old in prev.items():
        if jid in now:
            continue
        if old.get("active"):
            old["active"] = False
            old["expired_on"] = TODAY
        merged.append(old)   # expire, don't delete

    merged.sort(key=lambda j: (not j.get("active"), j.get("d") or "", j["co"]),
                reverse=False)
    merged.sort(key=lambda j: j.get("d") or "0000", reverse=True)
    merged.sort(key=lambda j: not j.get("active"))
    return merged


def main():
    reg = json.loads(REG_PATH.read_text())
    harvest, errors = [], []
    for portal, scraper in SCRAPERS.items():
        for entry in reg.get(portal, []):
            if not entry.get("board"):
                continue
            try:
                jobs = scraper(entry)
                harvest.extend(jobs)
                print(f"{entry['name']}: {len(jobs)} Ireland roles")
            except Exception as e:
                errors.append(f"{entry['name']}: {e}")
                print(f"ERROR {entry['name']}: {e}", file=sys.stderr)

    if not harvest and errors:
        sys.exit("every source failed - keeping yesterday's data, not writing zeros")

    merged = merge(harvest)
    active = [j for j in merged if j.get("active")]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "active_roles": len(active),
        "companies": len({j["co"] for j in active}),
        "errors": errors,
        "jobs": merged,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"\nwrote {OUT_PATH}: {len(active)} active roles at "
          f"{len({j['co'] for j in active})} companies "
          f"({len(merged) - len(active)} kept as expired history)")


if __name__ == "__main__":
    main()
