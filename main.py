import os
import re
import json
import html
import math
import calendar
import sqlite3
import hashlib
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta, date
from typing import Literal, List
import requests
from pydantic import BaseModel
from google import genai
from google.genai import types
from jobspy import scrape_jobs

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
EVENT_NAME = os.environ.get("EVENT_NAME", "schedule")
DB_PATH = "seen_jobs.db"
AF_API_URL = "https://jobsearch.api.jobtechdev.se/search"
SERPAPI_URL = "https://serpapi.com/search.json"
BATCH_SIZE = 8
MAX_JOBS_PER_RUN = 100
DB_RETENTION_DAYS = 90
SERPAPI_TEST_RESERVE_RATIO = 0.15

# 1. Unlimited & Free: Expanded Arbetsförmedlingen (Platsbanken)
AF_QUERIES = [
    {"q": "industriell ekonomi", "limit": 40},
    {"q": "maskinteknik", "limit": 40},
    {"q": "civilingenjör trainee", "limit": 40},
    {"q": "graduate engineer", "limit": 40},
    {"q": "junior ingenjör", "limit": 40},
    {"q": "nyexaminerad ingenjör", "limit": 40},
    {"q": "trainee ingenjör", "limit": 40},
    {"q": "ingenjör Stockholm", "limit": 40},
    {"q": "engineer Stockholm", "limit": 40},
    {"q": "ingenjör Örebro", "limit": 40},
    {"q": "ingenjör Malmö", "limit": 40},
    {"q": "ingenjör Lund", "limit": 30},
    {"q": "ingenjör Södertälje", "limit": 30},
    {"q": "ingenjör Västerås", "limit": 30},
    {"q": "simuleringsingenjör", "limit": 40},
    {"q": "simulation engineer", "limit": 40},
    {"q": "beräkningsingenjör", "limit": 40},
    {"q": "produktionsingenjör", "limit": 40},
    {"q": "production engineer", "limit": 40},
    {"q": "processingenjör", "limit": 40},
    {"q": "kvalitetsingenjör", "limit": 40},
    {"q": "quality engineer", "limit": 40},
    {"q": "projektingenjör", "limit": 40},
    {"q": "project engineer", "limit": 40},
    {"q": "logistikingenjör", "limit": 40},
    {"q": "supply chain", "limit": 40},
    {"q": "driftingenjör", "limit": 30},
    {"q": "underhållsingenjör", "limit": 30},
    {"q": "reliability engineer", "limit": 30},
    {"q": "utvecklingsingenjör", "limit": 40},
    {"q": "R&D engineer", "limit": 40},
    {"q": "lean six sigma", "limit": 30},
    {"q": "verksamhetsutvecklare", "limit": 30},
]

# 2. Unlimited & Free: Verified Active Teamtailor RSS Feeds
TEAMTAILOR_FEEDS = [
    {"company": "ONE Nordic", "url": "https://karriar.one-nordic.se/jobs.rss"},
    {"company": "Combine Control Systems", "url": "https://combine.teamtailor.com/jobs.rss"},
    {"company": "AQ Engineering", "url": "https://aqengineering.teamtailor.com/jobs.rss"},
]

# 3. Free Direct Scraper: Expanded JobSpy (Indeed 7-day window; LinkedIn via SerpApi)
JOBSPY_QUERIES = [
    {
        "search_term": "simulation engineer OR production engineer OR graduate engineer",
        "location": "Stockholm, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "quality engineer OR project engineer OR supply chain OR R&D engineer",
        "location": "Stockholm, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "production engineer OR process engineer OR industrial engineer",
        "location": "Malmö, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "quality engineer OR project engineer OR supply chain OR operations analyst",
        "location": "Malmö, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "mechanical engineer OR production engineer OR project engineer",
        "location": "Örebro, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "simulation engineer OR production engineer OR graduate engineer",
        "location": "Copenhagen, Denmark",
        "country_indeed": "Denmark",
        "sites": ["indeed"],
    },
    {
        "search_term": "quality engineer OR project engineer OR supply chain OR operations",
        "location": "Copenhagen, Denmark",
        "country_indeed": "Denmark",
        "sites": ["indeed"],
    },
    {
        "search_term": "simulation engineer OR production engineer OR sivilingeniør",
        "location": "Oslo, Norway",
        "country_indeed": "Norway",
        "sites": ["indeed"],
    },
]

# 4. Quota-Paced: SerpApi Google X-Ray (Country-Locked gl + Single-Job URLs Only)
SERPAPI_XRAY_QUERIES = [
    {
        "q": 'site:linkedin.com/jobs/view ("simulation engineer" OR "production engineer" OR "graduate engineer") (Stockholm OR Malmö OR Örebro)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:*.teamtailor.com/jobs ("engineer" OR "ingenjör") (Stockholm OR Malmö OR Örebro OR Västerås OR Södertälje)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:se.indeed.com/viewjob ("simuleringsingenjör" OR "produktionsingenjör" OR "kvalitetsingenjör" OR "graduate engineer")',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("simulation engineer" OR "production engineer" OR "graduate engineer") Copenhagen',
        "gl": "dk",
        "fallback_region": "Copenhagen",
    },
    {
        "q": 'site:jobindex.dk/vis-job ("simulation engineer" OR "production engineer" OR "industrial engineer")',
        "gl": "dk",
        "fallback_region": "Copenhagen",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("simulation engineer" OR "production engineer" OR "graduate engineer") Oslo',
        "gl": "no",
        "fallback_region": "Oslo",
    },
    {
        "q": 'site:finn.no/job ("simuleringsingeniør" OR "produksjonsingeniør" OR "sivilingeniør" OR "graduate") Oslo',
        "gl": "no",
        "fallback_region": "Oslo",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("quality engineer" OR "project engineer" OR "supply chain" OR "lean engineer") (Stockholm OR Malmö OR Södertälje)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("mechanical engineer" OR "process engineer") (Örebro OR Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
]

EXCLUDED_TITLE_KEYWORDS = [
    # Search/Aggregator Directory Pages & Non-Target Portals
    "jobs i ",
    "jobs in ",
    "jobs near",
    "lediga jobb",
    "ziprecruiter",
    # Seniority & Non-Target IT/Network
    "senior ",
    "linux",
    "network",
    "säkerhet",
    "it-",
    # Pure Wet-Lab Chemistry, Biology & Food Science (keeps Pharma Process/Device Engineering!)
    "biokemist",
    "kemist",
    "kemiingenjör",
    "biolog",
    "mikrobiolog",
    "laboratorie",
    "labbingenjör",
    "farmaceut",
    "livsmedel",
    "food",
    # Construction, Civil, Building, HVAC & Electrical Infrastructure
    "bygg",
    "konstruktör",
    "konstruktion",
    "anläggning",
    "vvs",
    "elprojektering",
    "elkraft",
    "högspänning",
    "high-voltage",
    "hvdc",
    "telecommunications",
    "el och automation",
    "bim",
    "arkitekt",
    "platschef",
    "arbetsledare bygg",
    "fastighets",
    "kärnkraft",
    # Pure Software, Web, IT & Cloud Engineering
    "software",
    "mjukvaru",
    "utvecklare",
    "developer",
    "programmerare",
    "systemutvecklare",
    "webbutvecklare",
    "applikationsutvecklare",
    "cybersecurity",
    "data engineer",
    "ai engineer",
    "backend",
    "frontend",
    "full stack",
    "fullstack",
    "devops",
    "cloud engineer",
    "network engineer",
    "application specialist",
    "it-arkitekt",
    "it-konsult",
    # Defense & Military
    "försvarsmakten",
    "saab",
    "bae systems",
    "fmv",
    # Healthcare, Education & Pure Accounting
    "sjuksköterska",
    "läkare",
    "lärare",
    "undersköterska",
    "personlig assistent",
    "regulatory reporting",
    "financial controlling",
    "redovisningsekonom",
]

ALLOWED_LOCATIONS = [
    "stockholm",
    "solna",
    "sundbyberg",
    "kista",
    "nacka",
    "täby",
    "järfälla",
    "huddinge",
    "botkyrka",
    "södertälje",
    "uppsala",
    "västerås",
    "eskilstuna",
    "örebro",
    "kumla",
    "hallsberg",
    "karlskoga",
    "malmö",
    "lund",
    "helsingborg",
    "landskrona",
    "trelleborg",
    "staffanstorp",
    "lomma",
    "copenhagen",
    "köpenhamn",
    "københavn",
    "oslo",
    "remote",
    "distans",
    "hybrid",
]


# --- LEAN, KEYWORD-ONLY PYDANTIC SCHEMA ---
class JobEvaluation(BaseModel):
    job_index: int
    job_title: str
    company: str
    location: str
    work_model: Literal["On-site", "Hybrid", "Remote", "Not specified"] = "Not specified"
    language_required: str = "Not specified"
    posted_date_iso: str = ""
    deadline_date_iso: str = ""
    matching_skills: List[str] = []
    gaps_or_requirements: List[str] = []
    overall_score: int
    fit_verdict: Literal["APPLY", "CONSIDER", "SKIP"]
    match_rationale: str


class BatchEvaluation(BaseModel):
    evaluations: List[JobEvaluation]


SYSTEM_PROMPT = """
You are an expert technical recruiter in Scandinavia. Evaluate EVERY numbered job in the batch against the candidate's profile, preserve its exact `job_index`, and assign an `overall_score` (1-10) and `fit_verdict`.

### STRICT OUTPUT FORMATTING (KEYWORDS ONLY — NO FULL SENTENCES):
- `match_rationale`: 3 to 4 ultra-short keyword phrases separated by ` • ` (NO full sentences, e.g. "Dual M.Sc. IEM & Mech • Plant Simulation Fit • Entry-Level • Stockholm").
- `matching_skills`: Max 4 short keyword tags (2-4 words each).
- `gaps_or_requirements`: Max 2 short keyword tags (e.g. "2+ yrs exp", "Swedish C1", or ["None"]).
- `posted_date_iso`: Extract publication date in `YYYY-MM-DD` format from metadata or description if present, else `""`.
- `deadline_date_iso`: Extract application deadline ("sista ansökningsdag" / "apply by") in `YYYY-MM-DD` format from metadata or description if present, else `""`.

### CANDIDATE PROFILE:
- Name: Matin Ahmadi
- Degrees: Civilingenjör (M.Sc.) in Industrial Management & Engineering (300 ECTS) & M.Sc. in Mechanical Engineering (120 ECTS, dual degree) from Örebro University; B.Sc. Business Administration coursework complete; Exchange at BI Norwegian Business School (Oslo).
- Core Skills: Discrete-event simulation (Siemens Tecnomatix Plant Simulation / SimTalk), multi-domain dynamic simulation (Hopsan / TLM), MATLAB & Simulink, FMU/FMI, HIL, C++, Python, CAD (PTC Creo, SolidWorks, AutoCAD), Lean Six Sigma, Kaizen/PDCA, VSM, FMEA, SAP ERP, Power BI, GIS.
- Experience:
  * Epiroc Rock Drills AB (Master Thesis, Grade 5): Real-time hydromechanical simulation in Hopsan & C++ (7.6x faster than real-time), test-bench validation.
  * AstraZeneca (Degree Project): Discrete-event digital twin in Plant Simulation (4,100+ lab tests), scheduling optimization (-56% failure rate, -61.3% delay).
  * Örebro University (Teaching Assistant): Mechanics, structural mechanics, applied math, CAD.
  * Malmö stad (Project Assistant & Analyst): Financial modeling in Excel, supervised 15-19 interns, GIS asset management.
  * AkzoNobel & VA SYD: SAP ERP inventory management, GIS mapping.
- Languages: Swedish (Native), English (Fluent), Pashto/Dari (Fluent), Spanish (Basic).

### SCORING & EXCLUSION RULES:
- Domain & Page Exclusions (Score 1, `fit_verdict`: "SKIP"):
  1. Job board search-result pages, category lists, or directory aggregators (not a single concrete job posting).
  2. Defense, weapons, armament, or military industry.
  3. Civil engineering, building construction (byggnadsingenjör / konstruktion / VVS / anläggning), or real estate construction.
  4. Pure software development, web/app/cloud/IT engineering (software engineer / systemutvecklare). Note: Physical/industrial simulation, digital twins, HIL, and MATLAB/C++ modeling ARE relevant and should NOT be excluded.
  5. Pure wet-lab chemistry, analytical biochemistry, microbiology, or R&D molecular biology (biokemist / kemist / labbingenjör / mikrobiolog). Note: Pharmaceutical manufacturing, process engineering, medical device assembly, Lean/operations, and discrete-event simulation at pharma companies (e.g., AstraZeneca) ARE highly relevant and should be scored high.
- Primary Regions: Malmö, Örebro, Stockholm, and ~1h commute (Lund, Copenhagen, Södertälje, Karlskoga, Eskilstuna, Västerås, Helsingborg). Roles in other Swedish cities (e.g. Gothenburg) or outside Scandinavia should be scored <= 5 (`fit_verdict`: "SKIP") unless remote/hybrid from target regions.
- Oslo Exception: Roles in Oslo are eligible ONLY if `overall_score` is 9 or 10; otherwise set `fit_verdict` to "SKIP".
- 9-10 (`APPLY`): Graduate/entry-level/trainee (0-2 yrs exp) matching IEM or Mechanical Engineering, simulation, Lean, or operations in target regions.
- 7-8 (`CONSIDER`): Strong technical match with candidate's tools/projects (2-3+ yrs exp) or solid engineering/quality/project/supply chain role in Primary Regions.
- 6 (`CONSIDER`): Adjacent entry/mid-level engineering, reliability, R&D, or operations/analyst role in Primary Regions where dual M.Sc. IEM & Mechanical Engineering is a credible fit.
- 1-5 (`SKIP`): Weak overlap, 5+ yrs senior role, outside target locations, or excluded sector.
"""


# --- DATABASE, DATE, NORMALIZATION & FILTER HELPERS ---
def get_swedish_now() -> datetime:
    cet = timezone(timedelta(hours=1))
    return datetime.now(cet)


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_jobs ("
        "job_hash TEXT PRIMARY KEY, "
        "title TEXT, "
        "company TEXT, "
        "score INTEGER, "
        "source TEXT, "
        "apply_url TEXT, "
        "created_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta_state "
        "(key TEXT PRIMARY KEY, value INTEGER)"
    )

    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(seen_jobs)").fetchall()
    }
    for col_name, col_type in [
        ("source", "TEXT"),
        ("apply_url", "TEXT"),
        ("created_at", "TEXT"),
    ]:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE seen_jobs ADD COLUMN {col_name} {col_type}")

    cutoff_iso = (get_swedish_now() - timedelta(days=DB_RETENTION_DAYS)).strftime("%Y-%m-%d")
    deleted = conn.execute(
        "DELETE FROM seen_jobs WHERE created_at IS NOT NULL AND created_at != '' AND created_at < ?",
        (cutoff_iso,),
    ).rowcount
    if deleted:
        print(f"[DB] Pruned {deleted} expired rows older than {cutoff_iso}", flush=True)

    conn.commit()
    return conn


def parse_iso_date(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and math.isnan(val):
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s or s.lower() in ("none", "nan", "nat"):
        return ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    try:
        dt = parsedate_to_datetime(s)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def format_posted_badge(iso_str: str, today: date) -> str:
    parsed = parse_iso_date(iso_str)
    if not parsed:
        return "Ej angivet"
    try:
        d = datetime.strptime(parsed, "%Y-%m-%d").date()
        diff = (today - d).days
        if diff <= 0:
            rel = "Idag"
        elif diff == 1:
            rel = "1d sedan"
        else:
            rel = f"{diff}d sedan"
        return f"{parsed} ({rel})"
    except Exception:
        return parsed


def format_deadline_badge(iso_str: str, today: date) -> str:
    parsed = parse_iso_date(iso_str)
    if not parsed:
        return "Löpande / Ej angivet"
    try:
        d = datetime.strptime(parsed, "%Y-%m-%d").date()
        diff = (d - today).days
        if diff < 0:
            return f"{parsed} (Utgången)"
        if diff == 0:
            return f"⚠️ {parsed} (Idag!)"
        if diff <= 4:
            return f"⚠️ {parsed} ({diff}d kvar)"
        return f"{parsed} ({diff}d kvar)"
    except Exception:
        return parsed


def normalize_token(text: str) -> str:
    cleaned = text.lower().strip()
    cleaned = re.sub(
        r"\b(ab|as|aps|sweden|sverige|danmark|denmark|norway|norge|linkedin|indeed)\b",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[^a-z0-9åäöøæ]+", " ", cleaned)
    return " ".join(cleaned.split())


def get_job_hash(title: str, company: str, location: str) -> str:
    raw = f"{normalize_token(title)}|{normalize_token(company)}|{normalize_token(location)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def is_fuzzy_duplicate(
    title: str, company: str, existing_pairs: list[tuple[str, str]]
) -> bool:
    norm_title = normalize_token(title)
    norm_comp = normalize_token(company)
    if not norm_title:
        return False

    for ex_title, ex_comp in existing_pairs:
        same_company = (
            norm_comp == ex_comp
            or norm_comp in ("", "unknown", "se linkedin com", "www linkedin com", "se indeed com")
            or ex_comp in ("", "unknown", "se linkedin com", "www linkedin com", "se indeed com")
        )
        if not same_company:
            continue
        if SequenceMatcher(None, norm_title, ex_title).ratio() >= 0.82:
            return True
    return False


def is_target_location(location: str, title: str, description: str = "") -> bool:
    primary = f"{location} {title}".lower().strip()
    if any(loc in primary for loc in ALLOWED_LOCATIONS):
        return True
    if location.lower() in ("", "unknown", "sweden", "sverige", "nordics"):
        head = description[:600].lower()
        return any(loc in head for loc in ALLOWED_LOCATIONS)
    return False


def strip_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw_html or ""))
    return " ".join(text.split())


def clean_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and math.isnan(val):
        return ""
    return str(val).strip()


def enrich_xray_page(url: str, fallback_snippet: str) -> tuple[str, str, str]:
    """Fetches static JobPosting JSON-LD or meta description from a single-job URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return fallback_snippet, "", ""
        page_html = resp.text

        for match in re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            page_html,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(match.strip())
                items = data if isinstance(data, list) else [data]
                for obj in items:
                    if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                        desc = strip_html(obj.get("description") or "")
                        posted = parse_iso_date(obj.get("datePosted"))
                        valid_through = parse_iso_date(obj.get("validThrough"))
                        if len(desc) > 120:
                            return desc[:2800], posted, valid_through
            except Exception:
                continue

        meta_m = re.search(
            r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if meta_m:
            meta_desc = strip_html(meta_m.group(1))
            if len(meta_desc) > len(fallback_snippet):
                return f"{meta_desc}\nSnippet: {fallback_snippet}"[:2800], "", ""
    except Exception:
        pass
    return fallback_snippet, "", ""


def register_job(
    raw_item: dict,
    seen_in_batch: set,
    batch_title_pairs: list[tuple[str, str]],
    output_list: list[dict],
) -> bool:
    title = clean_str(raw_item.get("title"))
    company = clean_str(raw_item.get("company_name")) or "Unknown"
    location = clean_str(raw_item.get("location"))
    description = clean_str(raw_item.get("description"))
    if not title:
        return False

    title_lower = title.lower()
    if any(bad in title_lower for bad in EXCLUDED_TITLE_KEYWORDS):
        return False

    if not is_target_location(location, title, description):
        return False

    display_loc = location or clean_str(raw_item.get("fallback_region")) or "Target Region"
    j_hash = get_job_hash(title, company, display_loc)
    if j_hash in seen_in_batch:
        return False
    if is_fuzzy_duplicate(title, company, batch_title_pairs):
        return False

    seen_in_batch.add(j_hash)
    batch_title_pairs.append((normalize_token(title), normalize_token(company)))

    raw_item["hash"] = j_hash
    raw_item["title"] = title
    raw_item["company_name"] = company
    raw_item["location"] = display_loc
    raw_item["posted_date"] = parse_iso_date(raw_item.get("posted_date"))
    raw_item["deadline_date"] = parse_iso_date(raw_item.get("deadline_date"))
    raw_item["description"] = description[:2800]
    output_list.append(raw_item)
    return True


def get_serpapi_budget() -> tuple[int, str]:
    if not SERPAPI_KEY:
        return 0, "SerpApi key not configured"
    try:
        resp = requests.get(
            "https://serpapi.com/account.json",
            params={"api_key": SERPAPI_KEY},
            timeout=10,
        )
        data = resp.json()
        left = data.get("plan_searches_left")
        total = data.get("searches_per_month")
        if left is None or total is None:
            return 0, "Quota unavailable"

        reserve = int(total * SERPAPI_TEST_RESERVE_RATIO)
        spendable = max(0, left - reserve)

        now = get_swedish_now()
        last_day = calendar.monthrange(now.year, now.month)[1]
        days_left = max(1, last_day - now.day + 1)

        daily_budget = 0 if spendable <= 0 else max(1, spendable // days_left)
        if EVENT_NAME == "workflow_dispatch":
            daily_budget = min(daily_budget, 2)

        status = (
            f"{left}/{total} left "
            f"(15% test reserve: {reserve}, run budget: {daily_budget})"
        )
        return daily_budget, status
    except Exception as e:
        print(f"SerpApi quota check error: {e}", flush=True)
        return 0, "Quota unavailable"


# --- SCRAPER 1: ARBETSFÖRMEDLINGEN (PLATSBANKEN) ---
def fetch_af_jobs(
    seen_in_batch: set, batch_title_pairs: list[tuple[str, str]], output_list: list[dict]
) -> int:
    added = 0
    headers = {"Accept": "application/json"}
    published_after = (get_swedish_now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    for q_cfg in AF_QUERIES:
        params = {
            "q": q_cfg["q"],
            "limit": q_cfg["limit"],
            "published-after": published_after,
            "sort": "pubdate-desc",
        }
        try:
            resp = requests.get(AF_API_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits") or []
            print(f"[AF] '{q_cfg['q']}': {len(hits)} hits", flush=True)
            for hit in hits:
                ok = register_job(
                    {
                        "source": "Platsbanken",
                        "title": hit.get("headline") or "",
                        "company_name": (hit.get("employer") or {}).get("name") or "Unknown",
                        "location": (hit.get("workplace_address") or {}).get("municipality") or "",
                        "posted_date": hit.get("publication_date") or "",
                        "deadline_date": hit.get("application_deadline") or "",
                        "description": (hit.get("description") or {}).get("text") or "",
                        "apply_url": hit.get("webpage_url") or "https://arbetsformedlingen.se/platsbanken",
                    },
                    seen_in_batch,
                    batch_title_pairs,
                    output_list,
                )
                if ok:
                    added += 1
        except Exception as e:
            print(f"[AF] Error for '{q_cfg['q']}': {e}", flush=True)
    return added


# --- SCRAPER 2: TEAMTAILOR DIRECT RSS FEEDS ---
def fetch_teamtailor_jobs(
    seen_in_batch: set, batch_title_pairs: list[tuple[str, str]], output_list: list[dict]
) -> int:
    added = 0
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScoutBot/1.0)"}
    for feed in TEAMTAILOR_FEEDS:
        company = feed["company"]
        url = feed["url"]
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                print(f"[Teamtailor] {company}: HTTP {resp.status_code}", flush=True)
                continue
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            feed_added = 0
            for item in items:
                title = item.findtext("title") or ""
                link = item.findtext("link") or url.replace("/jobs.rss", "")
                pub_date = item.findtext("pubDate") or ""
                desc = strip_html(item.findtext("description") or "")
                categories = [c.text for c in item.findall("category") if c.text]
                location = ", ".join(categories) if categories else ""

                ok = register_job(
                    {
                        "source": f"Teamtailor ({company})",
                        "title": title,
                        "company_name": company,
                        "location": location,
                        "posted_date": pub_date,
                        "deadline_date": "",
                        "description": desc,
                        "apply_url": link,
                    },
                    seen_in_batch,
                    batch_title_pairs,
                    output_list,
                )
                if ok:
                    added += 1
                    feed_added += 1
            print(f"[Teamtailor] {company}: {len(items)} open, {feed_added} kept", flush=True)
        except Exception as e:
            print(f"[Teamtailor] Error for {company}: {e}", flush=True)
    return added


# --- SCRAPER 3: JOBSPY (INDEED 7-DAY FULL DESCRIPTIONS) ---
def fetch_jobspy_jobs(
    seen_in_batch: set, batch_title_pairs: list[tuple[str, str]], output_list: list[dict]
) -> int:
    added = 0
    for q_cfg in JOBSPY_QUERIES:
        try:
            df = scrape_jobs(
                site_name=q_cfg["sites"],
                search_term=q_cfg["search_term"],
                location=q_cfg["location"],
                results_wanted=25,
                hours_old=168,
                country_indeed=q_cfg["country_indeed"],
                fetch_description=True,
                verbose=0,
            )
            raw_count = len(df) if df is not None else 0
            q_added = 0
            if raw_count > 0:
                for row in df.to_dict(orient="records"):
                    site_label = clean_str(row.get("site")).capitalize() or "JobSpy"
                    apply_url = (
                        clean_str(row.get("job_url_direct"))
                        or clean_str(row.get("job_url"))
                        or "https://se.indeed.com"
                    )
                    ok = register_job(
                        {
                            "source": site_label,
                            "title": row.get("title"),
                            "company_name": row.get("company"),
                            "location": row.get("location") or q_cfg["location"],
                            "posted_date": row.get("date_posted"),
                            "deadline_date": "",
                            "description": row.get("description"),
                            "apply_url": apply_url,
                        },
                        seen_in_batch,
                        batch_title_pairs,
                        output_list,
                    )
                    if ok:
                        added += 1
                        q_added += 1
            print(f"[JobSpy] {q_cfg['location']}: {raw_count} scraped, {q_added} kept", flush=True)
            time.sleep(2)
        except Exception as e:
            print(f"[JobSpy] Warning for {q_cfg['location']}: {str(e)[:120]}", flush=True)
    return added


# --- SCRAPER 4: SERPAPI GOOGLE X-RAY (COUNTRY-LOCKED + FULL DESCRIPTION ENRICHMENT) ---
def fetch_serpapi_jobs(
    conn: sqlite3.Connection,
    daily_budget: int,
    seen_in_batch: set,
    batch_title_pairs: list[tuple[str, str]],
    output_list: list[dict],
) -> tuple[int, int]:
    if daily_budget <= 0 or not SERPAPI_KEY:
        print(f"[SerpApi] Skipping automated search (daily_budget={daily_budget}).", flush=True)
        return 0, 0

    row = conn.execute("SELECT value FROM meta_state WHERE key = 'serpapi_cursor'").fetchone()
    cursor = row[0] if row else 0

    num_queries = min(daily_budget, len(SERPAPI_XRAY_QUERIES))
    selected_queries = [
        SERPAPI_XRAY_QUERIES[(cursor + i) % len(SERPAPI_XRAY_QUERIES)]
        for i in range(num_queries)
    ]

    new_cursor = (cursor + num_queries) % len(SERPAPI_XRAY_QUERIES)
    sql_upsert = (
        "INSERT OR REPLACE INTO meta_state (key, value) "
        "VALUES ('serpapi_cursor', ?)"
    )
    conn.execute(sql_upsert, (new_cursor,))
    conn.commit()

    added = 0
    used = 0
    for q_cfg in selected_queries:
        params = {
            "engine": "google",
            "q": q_cfg["q"],
            "gl": q_cfg["gl"],
            "tbs": "qdr:w",
            "num": 10,
            "api_key": SERPAPI_KEY,
        }
        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=20)
            resp.raise_for_status()
            used += 1
            results = resp.json().get("organic_results") or []
            print(f"[SerpApi] '{q_cfg['q'][:45]}...': {len(results)} results", flush=True)
            for res in results:
                title = res.get("title") or ""
                snippet = res.get("snippet") or ""
                link = res.get("link") or ""
                res_date = res.get("date") or ""
                if not title or not link:
                    continue

                displayed = res.get("displayed_link") or "Web"
                company = displayed.split("/")[0]
                raw_desc = f"Published hint: {res_date}\nSource URL: {link}\nSnippet: {snippet}"

                ok = register_job(
                    {
                        "source": "SerpApi X-Ray",
                        "title": title,
                        "company_name": company,
                        "location": "",
                        "fallback_region": q_cfg["fallback_region"],
                        "posted_date": res_date,
                        "deadline_date": "",
                        "description": raw_desc,
                        "apply_url": link,
                    },
                    seen_in_batch,
                    batch_title_pairs,
                    output_list,
                )
                if ok:
                    added += 1
                    item = output_list[-1]
                    exists = conn.execute(
                        "SELECT 1 FROM seen_jobs WHERE job_hash = ?", (item["hash"],)
                    ).fetchone()
                    if not exists:
                        full_desc, json_posted, json_deadline = enrich_xray_page(link, raw_desc)
                        item["description"] = full_desc[:2800]
                        if json_posted and not item["posted_date"]:
                            item["posted_date"] = json_posted
                        if json_deadline and not item["deadline_date"]:
                            item["deadline_date"] = json_deadline
                        time.sleep(0.8)
        except Exception as e:
            print(f"[SerpApi] Error for query: {e}", flush=True)
    return added, used


# --- BATCH EVALUATE WITH 4-TIER MODEL FALLBACK & EXPONENTIAL 503 BACKOFF ---
def evaluate_batch(
    client: genai.Client, batch: list[dict], skip_primary: bool
) -> tuple[list[JobEvaluation], bool]:
    blocks = []
    for idx, job in enumerate(batch):
        posted_meta = job.get("posted_date") or "Unknown"
        deadline_meta = job.get("deadline_date") or "Unknown"
        block = (
            f"=== JOB_INDEX: {idx} ===\n"
            f"Source: {job.get('source')}\n"
            f"Title: {job.get('title')}\n"
            f"Company: {job.get('company_name')}\n"
            f"Location: {job.get('location')}\n"
            f"Posted Date: {posted_meta}\n"
            f"Application Deadline: {deadline_meta}\n"
            f"Description:\n{job.get('description', '')}"
        )
        blocks.append(block)
    prompt_text = "\n\n".join(blocks)

    models_to_try = (
        ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]
        if skip_primary
        else ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]
    )
    backoff_seconds = [10, 20, 35, 45]

    for attempt, model_name in enumerate(models_to_try):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=BatchEvaluation,
                    temperature=0.2,
                ),
            )
            evals = response.parsed.evaluations if response.parsed else []
            if evals:
                print(f"Batch evaluated ({len(evals)} jobs) via {model_name}", flush=True)
                return evals, skip_primary
        except Exception as e:
            err_str = str(e)
            print(f"Attempt {attempt + 1} ({model_name}) failed: {err_str[:140]}", flush=True)
            if model_name == "gemini-3.8-flash" and "RESOURCE_EXHAUSTED" in err_str:
                skip_primary = True
            time.sleep(backoff_seconds[min(attempt, len(backoff_seconds) - 1)])
    return [], skip_primary


def send_discord_embed(
    eval_data: JobEvaluation,
    job: dict,
    quota_text: str,
    run_date: str,
    today: date,
):
    if eval_data.overall_score >= 9:
        color = 0x2ECC71
        badge = "🟢 TOP MATCH"
    elif eval_data.overall_score >= 7:
        color = 0xF1C40F
        badge = "🟡"
    else:
        color = 0x3498DB
        badge = "🔵"

    apply_url = job.get("apply_url") or "https://arbetsformedlingen.se/platsbanken"
    source_name = job.get("source") or "Job Board"
    posted_iso = job.get("posted_date") or eval_data.posted_date_iso
    deadline_iso = job.get("deadline_date") or eval_data.deadline_date_iso

    posted_text = format_posted_badge(posted_iso, today)
    deadline_text = format_deadline_badge(deadline_iso, today)

    skills_list = eval_data.matching_skills[:4]
    skills_text = " • ".join([f"✅ {s}" for s in skills_list]) if skills_list else "Engineering Core"

    gaps_list = [g for g in eval_data.gaps_or_requirements[:2] if g.lower() != "none"]
    gaps_text = " • ".join([f"⚠️ {g}" for g in gaps_list]) if gaps_list else "None"

    embed = {
        "title": f"{badge} {eval_data.overall_score}/10 — {eval_data.job_title} ({eval_data.company})",
        "url": apply_url,
        "description": f"*{eval_data.match_rationale}*",
        "color": color,
        "footer": {"text": f"Source: {source_name} • Searched: {run_date} • SerpApi: {quota_text}"},
        "fields": [
            {"name": "📍 Location", "value": eval_data.location, "inline": True},
            {"name": "🏢 Model", "value": eval_data.work_model, "inline": True},
            {"name": "🗣️ Language", "value": eval_data.language_required, "inline": True},
            {"name": "📅 Publicerad", "value": posted_text, "inline": True},
            {"name": "⏳ Sista ansökan", "value": deadline_text, "inline": True},
            {"name": "🛠️ Key Matches", "value": skills_text[:1024], "inline": False},
            {"name": "📋 Watchouts / Req", "value": gaps_text[:1024], "inline": False},
        ],
    }

    payload = {"embeds": [embed]}
    if eval_data.overall_score >= 9:
        payload["content"] = f"🔥 **9–10/10 High-Priority Match:** {eval_data.job_title} @ {eval_data.company}"

    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        print(f"Discord webhook error {resp.status_code}: {resp.text}", flush=True)
    time.sleep(1.5)


def main():
    conn = init_db()
    now_cet = get_swedish_now()
    today = now_cet.date()
    today_iso = today.strftime("%Y-%m-%d")
    run_date = now_cet.strftime("%Y-%m-%d %H:%M CET")
    client = genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": 60000})

    daily_budget, budget_status = get_serpapi_budget()
    print(f"[{run_date}] SerpApi budget check -> {budget_status}", flush=True)

    seen_in_batch = set()
    batch_title_pairs = []
    raw_jobs = []
    af_count = fetch_af_jobs(seen_in_batch, batch_title_pairs, raw_jobs)
    tt_count = fetch_teamtailor_jobs(seen_in_batch, batch_title_pairs, raw_jobs)
    js_count = fetch_jobspy_jobs(seen_in_batch, batch_title_pairs, raw_jobs)
    serp_count, serp_used = fetch_serpapi_jobs(
        conn, daily_budget, seen_in_batch, batch_title_pairs, raw_jobs
    )
    print(
        f"Total unique fetched: {len(raw_jobs)} "
        f"(AF: {af_count}, Teamtailor: {tt_count}, JobSpy: {js_count}, SerpApi: {serp_count})",
        flush=True,
    )

    recent_cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    db_recent_rows = conn.execute(
        "SELECT title, company FROM seen_jobs "
        "WHERE created_at IS NULL OR created_at = '' OR created_at >= ?",
        (recent_cutoff,),
    ).fetchall()
    db_recent_pairs = [
        (normalize_token(r[0] or ""), normalize_token(r[1] or "")) for r in db_recent_rows
    ]

    all_unseen = []
    for job in raw_jobs:
        if conn.execute("SELECT 1 FROM seen_jobs WHERE job_hash = ?", (job["hash"],)).fetchone():
            continue
        if is_fuzzy_duplicate(job["title"], job["company_name"], db_recent_pairs):
            continue
        all_unseen.append(job)

    unseen_jobs = all_unseen[:MAX_JOBS_PER_RUN]
    print(
        f"Unseen jobs total: {len(all_unseen)} (evaluating up to {len(unseen_jobs)} this run)",
        flush=True,
    )

    matches = []
    evaluated_count = 0
    skip_primary = False

    for i in range(0, len(unseen_jobs), BATCH_SIZE):
        batch = unseen_jobs[i : i + BATCH_SIZE]
        evaluations, skip_primary = evaluate_batch(client, batch, skip_primary)
        for ev in evaluations:
            if 0 <= ev.job_index < len(batch):
                job = batch[ev.job_index]
                evaluated_count += 1
                print(
                    f"[{evaluated_count}] [{job['source']}] {job['title']} -> "
                    f"{ev.overall_score}/10 ({ev.fit_verdict})",
                    flush=True,
                )
                sql_insert = (
                    "INSERT OR IGNORE INTO seen_jobs "
                    "(job_hash, title, company, score, source, apply_url, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)"
                )
                conn.execute(
                    sql_insert,
                    (
                        job["hash"],
                        job["title"],
                        job["company_name"],
                        ev.overall_score,
                        job.get("source", ""),
                        job.get("apply_url", ""),
                        today_iso,
                    ),
                )
                conn.commit()

                if ev.fit_verdict != "SKIP" and ev.overall_score >= 6:
                    matches.append((ev, job))
        time.sleep(3)

    _, updated_quota_status = get_serpapi_budget()
    matches.sort(key=lambda x: x[0].overall_score, reverse=True)
    for eval_data, job_item in matches[:25]:
        send_discord_embed(eval_data, job_item, updated_quota_status, run_date, today)

    summary_text = (
        f"📊 **Daily Job Scout ({run_date}):** Evaluated **{evaluated_count}** new jobs "
        f"(AF: {af_count}, Teamtailor: {tt_count}, JobSpy: {js_count}, SerpApi: {serp_count}) • "
        f"Found **{len(matches)}** matches (6+) • "
        f"SerpApi: **{updated_quota_status}** (used {serp_used} today)"
    )
    requests.post(DISCORD_WEBHOOK_URL, json={"content": summary_text}, timeout=15)
    print(f"Done. Sent {min(len(matches), 25)} matching jobs to Discord.", flush=True)


if __name__ == "__main__":
    main()
