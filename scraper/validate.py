"""Validate ATS board tokens live and record the result in companies.json.

Semantics (deliberate, learned from run #1):
- a probe SUCCESS fills or confirms the entry's `board` and stamps
  `last_validated`
- a probe FAILURE never clears a previously working board — one bad morning
  at an ATS must not wipe the registry; the failure is logged loudly instead
- every failed candidate prints its actual error, so the workflow log says
  *why* a board didn't validate (403 = bot-blocked, 404 = wrong token, ...)
"""
import datetime as dt
import json
import pathlib
import sys
import time

from common import http_json

HERE = pathlib.Path(__file__).parent
REG_PATH = HERE / "companies.json"
TODAY = dt.date.today().isoformat()


def _candidates(entry):
    seen, out = set(), []
    for c in ([entry.get("board")] if entry.get("board") else []) + entry.get("candidates", []):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def probe_workday(entry):
    for site in _candidates(entry):
        url = f"https://{entry['host']}/wday/cxs/{entry['tenant']}/{site}/jobs"
        headers = {"Origin": f"https://{entry['host']}",
                   "Referer": f"https://{entry['host']}/en-US/{site}"}
        try:
            resp = http_json(url, body={"appliedFacets": {}, "limit": 1, "offset": 0},
                             retries=2, headers=headers)
            if isinstance(resp, dict) and "total" in resp:
                return site
            print(f"    {entry['name']} [{site}]: 200 but no 'total' in body", file=sys.stderr)
        except Exception as e:
            print(f"    {entry['name']} [{site}]: {e}", file=sys.stderr)
    return None


def probe_smartrecruiters(entry):
    for cid in _candidates(entry):
        url = f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=1"
        try:
            resp = http_json(url, retries=2)
            if isinstance(resp, dict) and "totalFound" in resp:
                return cid
        except Exception as e:
            print(f"    {entry['name']} [{cid}]: {e}", file=sys.stderr)
    return None


def probe_greenhouse(entry):
    for token in _candidates(entry):
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        try:
            resp = http_json(url, retries=2)
            if isinstance(resp, dict) and "jobs" in resp:
                return token
        except Exception as e:
            print(f"    {entry['name']} [{token}]: {e}", file=sys.stderr)
    return None


PROBES = {
    "workday": probe_workday,
    "smartrecruiters": probe_smartrecruiters,
    "greenhouse": probe_greenhouse,
}


def main():
    reg = json.loads(REG_PATH.read_text())
    pinned, ok, fail = 0, [], []
    for portal, probe in PROBES.items():
        for entry in reg.get(portal, []):
            if entry.get("board"):
                # already pinned - scrape.py is the real probe; don't burn a
                # second request burst on Workday's rate limiter re-proving it
                pinned += 1
                continue
            board = probe(entry)
            if board:
                entry["board"] = board
                entry["last_validated"] = TODAY
                ok.append(f"{entry['name']}")
            else:
                fail.append(f"{entry['name']} ({portal})")
            time.sleep(1)
    REG_PATH.write_text(json.dumps(reg, indent=2) + "\n")
    print(f"{pinned} boards pinned; newly validated {len(ok)}: {', '.join(ok) or '-'}")
    if fail:
        print(f"NOT validated, no board ({len(fail)}): {', '.join(fail)}", file=sys.stderr)


if __name__ == "__main__":
    main()
