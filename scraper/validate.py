"""Validate ATS board tokens live and write them back into companies.json.

Run on every refresh (it is ~40 cheap requests). A company enters the scrape
only with a validated board: no entry survives on a guess alone. Candidates
that fail every probe stay listed as candidates so a later fix is a data
edit, not a code change.
"""
import json
import pathlib
import sys
import urllib.error

from common import http_json

HERE = pathlib.Path(__file__).parent
REG_PATH = HERE / "companies.json"


def probe_workday(entry):
    for site in entry.get("candidates", []) + ([entry["board"]] if entry.get("board") else []):
        url = f"https://{entry['host']}/wday/cxs/{entry['tenant']}/{site}/jobs"
        try:
            resp = http_json(url, body={"appliedFacets": {}, "limit": 1, "offset": 0}, retries=2)
            if isinstance(resp, dict) and "total" in resp:
                return site
        except Exception:
            continue
    return None


def probe_smartrecruiters(entry):
    for cid in entry.get("candidates", []) + ([entry["board"]] if entry.get("board") else []):
        url = f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=1"
        try:
            resp = http_json(url, retries=2)
            if isinstance(resp, dict) and "totalFound" in resp:
                return cid
        except Exception:
            continue
    return None


def probe_greenhouse(entry):
    for token in entry.get("candidates", []) + ([entry["board"]] if entry.get("board") else []):
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        try:
            resp = http_json(url, retries=2)
            if isinstance(resp, dict) and "jobs" in resp:
                return token
        except Exception:
            continue
    return None


PROBES = {
    "workday": probe_workday,
    "smartrecruiters": probe_smartrecruiters,
    "greenhouse": probe_greenhouse,
}


def main():
    reg = json.loads(REG_PATH.read_text())
    ok, fail = [], []
    for portal, probe in PROBES.items():
        for entry in reg.get(portal, []):
            board = probe(entry)
            entry["board"] = board
            (ok if board else fail).append(f"{entry['name']} ({portal})")
    REG_PATH.write_text(json.dumps(reg, indent=2) + "\n")
    print(f"validated {len(ok)} boards: {', '.join(ok)}")
    if fail:
        print(f"NOT validated ({len(fail)}): {', '.join(fail)}", file=sys.stderr)


if __name__ == "__main__":
    main()
