"""Shared helpers: HTTP with retry/backoff, Ireland detection, tagging, fit flag."""
import json
import re
import time
import urllib.request
import urllib.error

UA = "Mozilla/5.0 (compatible; IrelandGMPBoard/1.0; personal job-search tool)"
TIMEOUT = 30


def http_json(url, body=None, retries=3, backoff=2.0):
    """GET (body=None) or POST JSON. Retries with exponential backoff on
    network errors, 5xx, 429, and malformed/empty JSON bodies (a 200 with a
    broken payload is treated as a failure, never as 'no results')."""
    last_err = None
    for attempt in range(retries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
            parsed = json.loads(raw)
            if parsed is None:
                raise ValueError("empty JSON body")
            return parsed
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                last_err = e
            else:
                raise  # 4xx other than 429: caller decides (invalid board etc.)
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
            last_err = e
        time.sleep(backoff * (2 ** attempt))
    raise RuntimeError(f"giving up on {url}: {last_err}")


# ---------------------------------------------------------------- Ireland ---

COUNTIES = [
    "Dublin", "Cork", "Limerick", "Galway", "Waterford", "Sligo", "Mayo",
    "Meath", "Louth", "Westmeath", "Kilkenny", "Carlow", "Wicklow",
    "Tipperary", "Kerry", "Clare", "Kildare", "Wexford", "Offaly", "Laois",
    "Longford", "Leitrim", "Roscommon", "Cavan", "Monaghan", "Donegal",
]
_TOWN_TO_COUNTY = {
    "dublin": "Dublin", "swords": "Dublin", "dun laoghaire": "Dublin",
    "dún laoghaire": "Dublin", "blanchardstown": "Dublin", "grange castle": "Dublin",
    "clondalkin": "Dublin", "tallaght": "Dublin", "northern cross": "Dublin",
    "damastown": "Dublin", "mulhuddart": "Dublin", "citywest": "Dublin",
    "cork": "Cork", "ringaskiddy": "Cork", "carrigtwohill": "Cork",
    "carrigtohill": "Cork", "little island": "Cork", "kinsale": "Cork",
    "currabinny": "Cork", "brinny": "Cork", "ballydine": "Tipperary",
    "clonmel": "Tipperary", "limerick": "Limerick", "raheen": "Limerick",
    "galway": "Galway", "loughrea": "Galway", "inverin": "Galway",
    "athenry": "Galway", "waterford": "Waterford", "dungarvan": "Waterford",
    "sligo": "Sligo", "westport": "Mayo", "castlebar": "Mayo",
    "swinford": "Mayo", "dunboyne": "Meath", "stamullen": "Meath",
    "stamullin": "Meath", "navan": "Meath", "trim": "Meath",
    "dundalk": "Louth", "drogheda": "Louth", "athlone": "Westmeath",
    "mullingar": "Westmeath", "kilkenny": "Kilkenny", "carlow": "Carlow",
    "bray": "Wicklow", "arklow": "Wicklow", "killorglin": "Kerry",
    "tralee": "Kerry", "ennis": "Clare", "shannon": "Clare",
    "newbridge": "Kildare", "naas": "Kildare", "maynooth": "Kildare",
    "cruiserath": "Dublin", "cootehill": "Cavan",
}


def looks_irish(text):
    if not text:
        return False
    t = text.lower()
    if "ireland" in t and "northern ireland" not in t:
        return True
    for town in _TOWN_TO_COUNTY:
        if town in t:
            return True
    for county in COUNTIES:
        if re.search(r"\b(co\.?\s+)?" + county.lower() + r"\b", t):
            return True
    return False


def county_of(text):
    if not text:
        return ""
    t = text.lower()
    for county in COUNTIES:
        if re.search(r"\b(co\.?|county)\s+" + county.lower() + r"\b", t):
            return county
    for town, county in _TOWN_TO_COUNTY.items():
        if town in t:
            return county
    for county in COUNTIES:
        if re.search(r"\b" + county.lower() + r"\b", t):
            return county
    return ""


# ---------------------------------------------------------------- tagging ---
# Categories match the board: qa | capa | aseptic | dsdp | val (multi-label).

_RULES = {
    "qa": [
        r"\bqa\b", r"quality assurance", r"quality specialist", r"quality engineer",
        r"compliance", r"quality systems?", r"quality system", r"deviation",
        r"investigat", r"qualified person", r"\bqp\b", r"quality control",
        r"\bqc\b", r"batch record", r"disposition", r"quality operations",
        r"sterility assurance", r"quality manager", r"product quality",
        r"manufacturing (compliance|systems)",
    ],
    "capa": [
        r"\bcapa\b", r"root cause", r"\brca\b", r"\bncr\b", r"non.?conformance",
        r"\bmsat\b", r"manufacturing science", r"technical excellence",
        r"tech(nology|nical)? transfer", r"process (scientist|support|specialist)",
        r"technical (services|operations|specialist|analytical)",
        r"continuous improvement", r"\bopex\b",
    ],
    "aseptic": [
        r"aseptic", r"sterile", r"fill.?finish", r"\bfilling\b", r"parenteral",
        r"syringe", r"lyophili[sz]", r"visual inspection", r"isolator",
        r"contamination control", r"sterility", r"environmental monitoring",
        r"grade [ab]\b", r"media fill", r"annex ?1",
    ],
    "dsdp": [
        r"drug substance", r"drug product", r"upstream", r"downstream",
        r"bioprocess", r"biologics", r"cell culture", r"purification",
        r"chromatography", r"formulation", r"\bapi\b", r"biopharma",
        r"manufacturing (associate|technician|specialist|process)",
        r"process (engineer|equipment)", r"bioreactor", r"\buf.?df\b",
    ],
    "val": [
        r"validation", r"\bcqv\b", r"\bcsv\b", r"qualification",
        r"\biq\b.*\boq\b", r"commissioning", r"c&q",
    ],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in _RULES.items()}

# Titles that match a rule above but are not GMP quality/manufacturing work.
_EXCLUDE = re.compile(
    r"sales|marketing|brand|commercial|account manager|finance|accountant|"
    r"payroll|recruiter|talent|hr\b|human resources|legal|counsel|"
    r"software|data (scientist|engineer|analyst)|cyber|security engineer|"
    r"warehouse|logistics|supply chain planner|procurement|buyer|"
    r"ehs|environmental health|safety (officer|manager)|"
    r"medical (affairs|science liaison)|clinical (trial|research)|"
    r"regulatory affairs publishing|pharmacovigilance|"
    r"receptionist|administrat|executive assistant|intern\b|graduate program",
    re.I,
)


def tag(title, description=""):
    """Return sorted category list for a posting; [] means not board-worthy."""
    text = f"{title} {description[:4000]}"
    if _EXCLUDE.search(title):
        return []
    cats = [k for k, pats in _COMPILED.items() if any(p.search(text) for p in pats)]
    # description-only matches are weaker: require the title itself to look
    # like manufacturing/quality work, or at least one title-level hit
    title_hit = any(p.search(title) for pats in _COMPILED.values() for p in pats)
    if not title_hit and description:
        gate = re.search(
            r"engineer|specialist|scientist|technician|supervisor|manager|"
            r"associate|analyst|lead\b", title, re.I)
        if not gate:
            return []
    return sorted(set(cats))


_FIT = re.compile(
    r"(qa|quality( assurance)?|compliance|quality system) specialist|"
    r"specialist,? (qa|quality)|senior specialist qa|"
    r"deviation|capa|investigat|sterility assurance|aseptic (quality|mqa)|"
    r"tech(nology|nical)? transfer lead|cleaning validation|"
    r"cqv|process validation|aseptic process",
    re.I,
)
_FIT_BLOCK = re.compile(
    r"director|\bvp\b|vice president|head of|technician|operator|"
    r"co-?op|intern\b|apprentice", re.I)


def fit_flag(title):
    return bool(_FIT.search(title)) and not _FIT_BLOCK.search(title)
