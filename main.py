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

# --- ENVIRONMENT & CORE CONFIG ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DISCORD_WEBHOOK_URL_MATIN = os.environ["DISCORD_WEBHOOK_URL"]
DISCORD_WEBHOOK_URL_ANTHON = os.environ.get("DISCORD_WEBHOOK_URL_ANTHON", "")
SERPAPI_KEY_1 = os.environ.get("SERPAPI_KEY", "")      # Renews 12th each month (100% Matin)
SERPAPI_KEY_2 = os.environ.get("SERPAPI_KEY_2", "")    # Renews 20th each month (50% Matin / 50% Anthon)
EVENT_NAME = os.environ.get("EVENT_NAME", "schedule")

MATIN_DISCORD_USER_ID = "1274373996850184374"          # @grenate6514
DB_PATH = "seen_jobs.db"
AF_API_URL = "https://jobsearch.api.jobtechdev.se/search"
SERPAPI_URL = "https://serpapi.com/search.json"
BATCH_SIZE = 6
MAX_JOBS_PER_PROFILE = 90
DB_RETENTION_DAYS = 90
SERPAPI_TEST_RESERVE_RATIO = 0.15

# --- LOCATION LISTS ---
STOCKHOLM_LOCATIONS = [
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
]

NON_STOCKHOLM_LOCATIONS = [
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
]

MATIN_ALLOWED_LOCATIONS = STOCKHOLM_LOCATIONS + NON_STOCKHOLM_LOCATIONS + [
    "remote",
    "distans",
    "hybrid",
]

ANTHON_ALLOWED_LOCATIONS = STOCKHOLM_LOCATIONS

# --- EXCLUSION KEYWORDS ---
COMMON_EXCLUDED_KEYWORDS = [
    # Search/Aggregator Directory Pages, News & Non-Target Portals
    "jobs i ",
    "jobs in ",
    "jobs near",
    "lediga jobb",
    "ziprecruiter",
    "presskonferens",
    "redhawks",
    # Seniority & Non-Target IT/Network
    "senior ",
    "linux",
    "network",
    "säkerhet",
    "it-",
    # Pure Wet-Lab Chemistry, Biology & Food Science
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

DEFENSE_KEYWORDS = [
    "försvarsmakten",
    "saab",
    "bae systems",
    "fmv",
    "foi",
]

# Matin excludes defense; Anthon allows defense!
MATIN_EXCLUDED_KEYWORDS = COMMON_EXCLUDED_KEYWORDS + DEFENSE_KEYWORDS
ANTHON_EXCLUDED_KEYWORDS = COMMON_EXCLUDED_KEYWORDS

# --- 1. FREE API: ARBETSFÖRMEDLINGEN (PLATSBANKEN) ---
AF_QUERIES = [
    {"q": "industriell ekonomi", "limit": 40},
    {"q": "maskinteknik", "limit": 40},
    {"q": "civilingenjör trainee", "limit": 40},
    {"q": "graduate engineer", "limit": 40},
    {"q": "graduate program", "limit": 40},
    {"q": "traineeprogram", "limit": 40},
    {"q": "management trainee", "limit": 40},
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
    {"q": "projektledare ingenjör", "limit": 40},
    {"q": "logistikingenjör", "limit": 40},
    {"q": "supply chain", "limit": 40},
    {"q": "supply planner", "limit": 30},
    {"q": "inköpare teknisk", "limit": 30},
    {"q": "hållbarhetsingenjör", "limit": 30},
    {"q": "sustainability engineer", "limit": 30},
    {"q": "driftingenjör", "limit": 30},
    {"q": "underhållsingenjör", "limit": 30},
    {"q": "reliability engineer", "limit": 30},
    {"q": "utvecklingsingenjör", "limit": 40},
    {"q": "R&D engineer", "limit": 40},
    {"q": "lean six sigma", "limit": 30},
    {"q": "verksamhetsutvecklare", "limit": 30},
]

# --- 2. FREE RSS: TEAMTAILOR FEEDS ---
TEAMTAILOR_FEEDS = [
    {"company": "ONE Nordic", "url": "https://karriar.one-nordic.se/jobs.rss"},
    {"company": "Combine Control Systems", "url": "https://combine.teamtailor.com/jobs.rss"},
    {"company": "AQ Engineering", "url": "https://aqengineering.teamtailor.com/jobs.rss"},
]

# --- 3. FREE SCRAPER: JOBSPY (INDEED 7-DAY WINDOW) ---
JOBSPY_QUERIES = [
    {
        "search_term": "simulation engineer OR production engineer OR graduate engineer",
        "location": "Stockholm, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "trainee OR quality engineer OR project engineer OR supply chain OR sustainability",
        "location": "Stockholm, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "production engineer OR process engineer OR graduate engineer",
        "location": "Malmö, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "trainee OR quality engineer OR project engineer OR supply chain",
        "location": "Malmö, Sweden",
        "country_indeed": "Sweden",
        "sites": ["indeed"],
    },
    {
        "search_term": "mechanical engineer OR production engineer OR graduate OR trainee",
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
        "search_term": "trainee OR quality engineer OR project engineer OR supply chain",
        "location": "Copenhagen, Denmark",
        "country_indeed": "Denmark",
        "sites": ["indeed"],
    },
    {
        "search_term": "simulation engineer OR production engineer OR sivilingeniør OR graduate",
        "location": "Oslo, Norway",
        "country_indeed": "Norway",
        "sites": ["indeed"],
    },
]

# --- 4A. MATIN SERPAPI X-RAY QUERIES (75% NON-STOCKHOLM / 25% STOCKHOLM) ---
MATIN_XRAY_NON_STHLM = [
    {
        "q": 'site:linkedin.com/jobs/view ("graduate engineer" OR "graduate program" OR "trainee") (Malmö OR Lund OR Örebro OR Västerås)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("simulation engineer" OR "production engineer" OR "process engineer") (Malmö OR Lund OR Örebro)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:se.indeed.com/viewjob ("simuleringsingenjör" OR "produktionsingenjör" OR "kvalitetsingenjör" OR "graduate") (Malmö OR Örebro OR Västerås OR Lund)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:*.teamtailor.com/jobs ("engineer" OR "ingenjör" OR "graduate" OR "trainee") (Malmö OR Lund OR Örebro OR Västerås OR Eskilstuna)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("quality engineer" OR "project engineer" OR "supply chain" OR "lean") (Malmö OR Lund OR Örebro OR Helsingborg)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("mechanical engineer" OR "R&D engineer" OR "manufacturing engineer") (Örebro OR Karlskoga OR Västerås OR Malmö)',
        "gl": "se",
        "fallback_region": "Sweden",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("graduate engineer" OR "graduate programme" OR "trainee") Copenhagen',
        "gl": "dk",
        "fallback_region": "Copenhagen",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("simulation engineer" OR "production engineer" OR "process engineer") Copenhagen',
        "gl": "dk",
        "fallback_region": "Copenhagen",
    },
    {
        "q": 'site:jobindex.dk/vis-job ("graduate" OR "trainee" OR "production engineer" OR "industrial engineer")',
        "gl": "dk",
        "fallback_region": "Copenhagen",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("graduate engineer" OR "trainee" OR "sivilingeniør") Oslo',
        "gl": "no",
        "fallback_region": "Oslo",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("simulation engineer" OR "production engineer" OR "process engineer") Oslo',
        "gl": "no",
        "fallback_region": "Oslo",
    },
    {
        "q": 'site:finn.no/job ("simuleringsingeniør" OR "produksjonsingeniør" OR "sivilingeniør" OR "graduate" OR "trainee") Oslo',
        "gl": "no",
        "fallback_region": "Oslo",
    },
]

MATIN_XRAY_STHLM = [
    {
        "q": 'site:linkedin.com/jobs/view ("graduate engineer" OR "traineeprogram" OR "simulation engineer") (Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("production engineer" OR "process engineer" OR "quality engineer") (Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:*.teamtailor.com/jobs ("engineer" OR "ingenjör" OR "graduate") (Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:se.indeed.com/viewjob ("produktionsingenjör" OR "processingenjör" OR "simuleringsingenjör") (Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
]

# --- 4B. ANTHON SERPAPI X-RAY QUERIES (100% STOCKHOLM AREA, INCL. DEFENSE & SUSTAINABILITY) ---
ANTHON_XRAY_STHLM = [
    {
        "q": 'site:linkedin.com/jobs/view ("industrial engineering" OR "industriell ekonomi" OR "management trainee" OR "graduate program") (Stockholm OR Södertälje OR Solna)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("project engineer" OR "projektingenjör" OR "junior project manager" OR "projektledare") (Stockholm OR Södertälje OR Kista)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("supply chain" OR "logistics" OR "logistikkonsult" OR "supply planner" OR "procurement") (Stockholm OR Södertälje OR Uppsala)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("sustainability engineer" OR "sustainability analyst" OR "hållbarhet" OR "environmental") (Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("production engineer" OR "process engineer" OR "quality engineer" OR "continuous improvement") (Stockholm OR Södertälje OR Uppsala)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("product development" OR "produktutveckling" OR "business development" OR "operations analyst") (Stockholm OR Solna OR Nacka)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:*.teamtailor.com/jobs ("industriell ekonomi" OR "project" OR "supply chain" OR "sustainability" OR "trainee" OR "graduate") (Stockholm OR Södertälje OR Solna)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:se.indeed.com/viewjob ("industriell ekonomi" OR "projektingenjör" OR "logistik" OR "supply chain" OR "hållbarhet") (Stockholm OR Södertälje)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
    {
        "q": 'site:linkedin.com/jobs/view ("Saab" OR "FMV" OR "FOI" OR "defense") ("engineer" OR "ingenjör" OR "project" OR "logistics" OR "graduate") (Stockholm OR Järfälla OR Solna)',
        "gl": "se",
        "fallback_region": "Stockholm",
    },
]


# --- PYDANTIC SCHEMAS ---
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


MATIN_SYSTEM_PROMPT = """
You are an expert technical recruiter in Scandinavia. Evaluate EVERY numbered job in the batch against Matin Ahmadi's profile, preserve its exact `job_index`, and assign an `overall_score` (1-10) and `fit_verdict`.

### STRICT OUTPUT FORMATTING (KEYWORDS ONLY — NO FULL SENTENCES):
- `match_rationale`: 3 to 4 ultra-short keyword phrases separated by ` • ` (NO full sentences, e.g. "Dual M.Sc. IEM & Mech • Plant Simulation Fit • Entry-Level • Stockholm").
- `matching_skills`: Max 4 short keyword tags (2-4 words each).
- `gaps_or_requirements`: Max 2 short keyword tags (e.g. "2+ yrs exp", "Swedish C1", or ["None"]).
- `posted_date_iso`: Extract publication date in `YYYY-MM-DD` format from metadata or description if present, else `""`.
- `deadline_date_iso`: Extract application deadline ("sista ansökningsdag" / "apply by") in `YYYY-MM-DD` format from metadata or description if present, else `""`.

### CANDIDATE PROFILE (MATIN AHMADI):
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
- 1-6 (`SKIP`): Weak overlap, 5+ yrs senior role, outside target locations, or excluded sector.
"""

ANTHON_SYSTEM_PROMPT = """
You are an expert technical recruiter in Stockholm, Sweden. Evaluate EVERY numbered job in the batch against Anthon Yao's profile, preserve its exact `job_index`, and assign an `overall_score` (1-10) and `fit_verdict`.

### STRICT OUTPUT FORMATTING (KEYWORDS ONLY — NO FULL SENTENCES):
- `match_rationale`: 3 to 4 ultra-short keyword phrases separated by ` • ` (NO full sentences, e.g. "M.Sc. IEM • Supply Chain & Project Mgmt • Entry-Level • Stockholm").
- `matching_skills`: Max 4 short keyword tags (2-4 words each).
- `gaps_or_requirements`: Max 2 short keyword tags (e.g. "2+ yrs exp", "Swedish C1", or ["None"]).
- `posted_date_iso`: Extract publication date in `YYYY-MM-DD` format from metadata or description if present, else `""`.
- `deadline_date_iso`: Extract application deadline ("sista ansökningsdag" / "apply by") in `YYYY-MM-DD` format from metadata or description if present, else `""`.

### CANDIDATE PROFILE (ANTHON YAO):
- Degree: Civilingenjör (M.Sc.) in Industrial Engineering & Management (300 ECTS, 2021–2026) from Örebro University, with focus on technology-based business development, mechanical engineering, product development, finance, project management, strategic marketing, and cross-functional collaboration.
- Core Skills: Discrete-event simulation (Siemens Tecnomatix Plant Simulation), MATLAB, Microsoft Excel/Office, supply chain & warehouse logistics, bottleneck analysis, sustainability assessment frameworks, project management, business development.
- Experience:
  * ASSA ABLOY Group (Master Thesis, Jan–Jun 2026, Stockholm): Developing a lifecycle sustainability assessment tool/framework for software development processes, translating sustainability goals into measurable operational criteria.
  * Dagab (Logistics Consultant, Feb–Aug 2025, Örebro): Warehouse management, quality assurance, supply chain punctuality, and daily workflow bottleneck resolution.
  * AstraZeneca (Project Leader / Degree Project, Oct 2024–Jan 2025, Södertälje): Led a 12-week QC lab process flow optimization using Siemens Tecnomatix Plant Simulation (5% margin of error) and proposed sustainable improvements.
  * Erasmus Student Network (ESN): International student coordinator and event organizer.
- Languages: Swedish (Fluent), English (Fluent).

### SCORING & EXCLUSION RULES:
- Location Rule: Must be in the **Stockholm area** (Stockholm, Solna, Sundbyberg, Kista, Nacka, Täby, Järfälla, Huddinge, Botkyrka, Södertälje, Uppsala). Roles outside the Stockholm area must be scored <= 4 (`fit_verdict`: "SKIP").
- Domain & Page Exclusions (Score 1, `fit_verdict`: "SKIP"):
  1. Job board search-result pages, category lists, or directory aggregators (not a single concrete job posting).
  2. Pure software development, web/app/cloud/IT engineering (software engineer / systemutvecklare). Note: Sustainability assessment frameworks, technical product management, industrial simulation, and industrial engineering ARE relevant.
  3. Pure wet-lab chemistry, analytical biochemistry, microbiology, or R&D molecular biology (biokemist / kemist / labbingenjör / mikrobiolog). Note: Pharma manufacturing, QC process optimization, supply chain, and operations at pharma companies ARE highly relevant.
  4. Civil engineering, building construction (byggnadsingenjör / konstruktion / VVS / elkraft / anläggning), or real estate construction.
- Allowed Industries: All industrial, manufacturing, pharma/medtech, energy, logistics, automotive, tech hardware, consulting, AND **defense/aerospace** (e.g., Saab, FMV, FOI, BAE Systems) are welcomed!
- 9-10 (`APPLY`): Graduate/trainee/entry-level (0-2 yrs exp) in Industrial Engineering & Management, project engineering/management, supply chain/logistics, sustainability engineering, production/process/quality engineering, or business/product development in the Stockholm area.
- 7-8 (`CONSIDER`): Solid engineering, operations, analyst, or project role in the Stockholm area (2-3+ yrs exp) with strong overlap with Anthon's M.Sc. IEM profile.
- 1-6 (`SKIP`): Weak overlap, 5+ yrs senior role, outside Stockholm area, or excluded sector.
"""


# --- DATE, DATABASE & DEDUPLICATION HELPERS ---
def get_swedish_now() -> datetime:
    cet = timezone(timedelta(hours=1))
    return datetime.now(cet)


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_jobs ("
        "job_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "job_hash TEXT, "
        "profile TEXT DEFAULT 'matin', "
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

    cols_info = conn.execute("PRAGMA table_info(seen_jobs)").fetchall()
    col_names = {row[1] for row in cols_info}

    if "job_id" not in col_names:
        print("[DB] Migrating seen_jobs to include auto-incrementing job_id & profile...", flush=True)
        conn.execute("ALTER TABLE seen_jobs RENAME TO seen_jobs_legacy")
        conn.execute(
            "CREATE TABLE seen_jobs ("
            "job_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_hash TEXT, "
            "profile TEXT DEFAULT 'matin', "
            "title TEXT, "
            "company TEXT, "
            "score INTEGER, "
            "source TEXT, "
            "apply_url TEXT, "
            "created_at TEXT"
            ")"
        )
        legacy_cols = {row[1] for row in conn.execute("PRAGMA table_info(seen_jobs_legacy)").fetchall()}
        src_expr = "source" if "source" in legacy_cols else "''"
        url_expr = "apply_url" if "apply_url" in legacy_cols else "''"
        dt_expr = "created_at" if "created_at" in legacy_cols else "''"
        conn.execute(
            f"INSERT INTO seen_jobs (job_id, job_hash, profile, title, company, score, source, apply_url, created_at) "
            f"SELECT rowid, job_hash, 'matin', title, company, score, {src_expr}, {url_expr}, {dt_expr} "
            f"FROM seen_jobs_legacy ORDER BY rowid ASC"
        )
        conn.execute("DROP TABLE seen_jobs_legacy")

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_jobs_profile_hash "
        "ON seen_jobs(profile, job_hash)"
    )

    cutoff_iso = (get_swedish_now() - timedelta(days=DB_RETENTION_DAYS)).strftime("%Y-%m-%d")
    deleted = conn.execute(
        "DELETE FROM seen_jobs WHERE created_at IS NOT NULL AND created_at != '' AND created_at < ?",
        (cutoff_iso,),
    ).rowcount
    if deleted:
        print(f"[DB] Pruned {deleted} expired rows older than {cutoff_iso}", flush=True)

    conn.commit()
    return conn


def save_seen_job(
    conn: sqlite3.Connection,
    profile: str,
    job: dict,
    score: int,
    today_iso: str,
) -> int:
    """Inserts job for profile if unseen and returns its persistent integer job_id."""
    row = conn.execute(
        "SELECT job_id FROM seen_jobs WHERE profile = ? AND job_hash = ?",
        (profile, job["hash"]),
    ).fetchone()
    if row:
        return row[0]

    cur = conn.execute(
        "INSERT INTO seen_jobs (job_hash, profile, title, company, score, source, apply_url, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job["hash"],
            profile,
            job["title"],
            job["company_name"],
            score,
            job.get("source", ""),
            job.get("apply_url", ""),
            today_iso,
        ),
    )
    conn.commit()
    return cur.lastrowid


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


def is_non_job_landing_title(title: str) -> bool:
    """Filters out bare municipality/city landing pages like 'Lunds kommun' or 'Västerås - Västerås'."""
    cleaned = re.sub(r"[^a-zåäöøæ\s]", " ", title.lower())
    words = [w for w in cleaned.split() if w not in ("kommun", "stad", "region", "län", "county")]
    unique_words = set(words)
    if not unique_words:
        return True
    all_cities = set(STOCKHOLM_LOCATIONS + NON_STOCKHOLM_LOCATIONS)
    if unique_words.issubset(all_cities):
        return True
    return False


def matches_location_list(
    location: str, title: str, description: str, allowed_list: list[str]
) -> bool:
    primary = f"{location} {title}".lower().strip()
    if any(loc in primary for loc in allowed_list):
        return True
    if location.lower() in ("", "unknown", "sweden", "sverige", "nordics"):
        head = description[:600].lower()
        return any(loc in head for loc in allowed_list)
    return False


def is_stockholm_job(job: dict) -> bool:
    return matches_location_list(
        job.get("location", ""),
        job.get("title", ""),
        job.get("description", ""),
        STOCKHOLM_LOCATIONS,
    )


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
    allowed_locations: list[str],
    excluded_keywords: list[str],
) -> bool:
    title = clean_str(raw_item.get("title"))
    company = clean_str(raw_item.get("company_name")) or "Unknown"
    location = clean_str(raw_item.get("location"))
    description = clean_str(raw_item.get("description"))
    if not title or is_non_job_landing_title(title):
        return False

    title_lower = title.lower()
    if any(bad in title_lower for bad in excluded_keywords):
        return False

    if not matches_location_list(location, title, description, allowed_locations):
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


# --- SERPAPI DUAL-KEY QUOTA PACING (RENEW DAY 12 & DAY 20, TUE-SAT RUNS) ---
def get_next_renewal_date(today: date, renew_day: int) -> date:
    """Returns the next occurrence of `renew_day` (e.g. 12th or 20th) strictly after `today`."""
    if today.day < renew_day:
        last_day = calendar.monthrange(today.year, today.month)[1]
        return date(today.year, today.month, min(renew_day, last_day))
    next_month = today.month + 1
    next_year = today.year
    if next_month > 12:
        next_month = 1
        next_year += 1
    last_day = calendar.monthrange(next_year, next_month)[1]
    return date(next_year, next_month, min(renew_day, last_day))


def count_tue_sat_runs_until(today: date, renewal_date: date) -> int:
    """Counts remaining Tue-Sat morning runs from `today` up to the day before `renewal_date`."""
    active_days = 0
    cursor = today
    while cursor < renewal_date:
        # Python weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
        if cursor.weekday() in (1, 2, 3, 4, 5):
            active_days += 1
        cursor += timedelta(days=1)
    return max(1, active_days)


def inspect_serpapi_account(api_key: str, renew_day: int, label: str) -> dict:
    if not api_key:
        return {"left": 0, "total": 0, "spendable": 0, "daily": 0, "days_left": 1, "label": label}
    try:
        resp = requests.get(
            "https://serpapi.com/account.json",
            params={"api_key": api_key},
            timeout=10,
        )
        data = resp.json()
        left = int(data.get("plan_searches_left") or 0)
        total = int(data.get("searches_per_month") or 250)
        reserve = int(total * SERPAPI_TEST_RESERVE_RATIO)
        spendable = max(0, left - reserve)

        today = get_swedish_now().date()
        renewal_date = get_next_renewal_date(today, renew_day)
        days_left = count_tue_sat_runs_until(today, renewal_date)
        daily = 0 if spendable <= 0 else max(1, spendable // days_left)
        return {
            "left": left,
            "total": total,
            "reserve": reserve,
            "spendable": spendable,
            "daily": daily,
            "days_left": days_left,
            "renewal": renewal_date.strftime("%Y-%m-%d"),
            "label": label,
        }
    except Exception as e:
        print(f"[SerpApi] Quota check error for {label}: {e}", flush=True)
        return {"left": 0, "total": 0, "spendable": 0, "daily": 0, "days_left": 1, "label": label}


def compute_dual_budgets() -> dict:
    """
    Key 1 (renews 12th): 100% Matin (250/mo -> ~213 spendable)
    Key 2 (renews 20th): 50% Matin / 50% Anthon (125/mo -> ~106 spendable each)
    """
    acc1 = inspect_serpapi_account(SERPAPI_KEY_1, renew_day=12, label="Key1(Matin)")
    acc2 = inspect_serpapi_account(SERPAPI_KEY_2, renew_day=20, label="Key2(Shared)")

    k1_for_matin = acc1["daily"]
    k2_half_daily = 0 if acc2["spendable"] <= 0 else max(1, (acc2["spendable"] // 2) // acc2["days_left"])
    if acc2["spendable"] <= 1:
        k2_half_daily = 0

    k2_for_matin = k2_half_daily
    k2_for_anthon = k2_half_daily

    if EVENT_NAME == "workflow_dispatch":
        k1_for_matin = min(k1_for_matin, 1)
        k2_for_matin = min(k2_for_matin, 1)
        k2_for_anthon = min(k2_for_anthon, 1)

    matin_total_daily = k1_for_matin + k2_for_matin
    anthon_total_daily = k2_for_anthon

    status_summary = (
        f"K1:{acc1['left']}/{acc1['total']}(ren {acc1.get('renewal','?')}) | "
        f"K2:{acc2['left']}/{acc2['total']}(ren {acc2.get('renewal','?')})"
    )
    return {
        "acc1": acc1,
        "acc2": acc2,
        "k1_matin": k1_for_matin,
        "k2_matin": k2_for_matin,
        "matin_daily": matin_total_daily,
        "anthon_daily": anthon_total_daily,
        "status_summary": status_summary,
    }


# --- SCRAPER 1: ARBETSFÖRMEDLINGEN (PLATSBANKEN) ---
def fetch_af_jobs(
    seen_in_batch: set,
    batch_title_pairs: list[tuple[str, str]],
    output_list: list[dict],
    allowed_locations: list[str],
    excluded_keywords: list[str],
    queries: list[dict],
) -> int:
    added = 0
    headers = {"Accept": "application/json"}
    published_after = (get_swedish_now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    for q_cfg in queries:
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
                    allowed_locations,
                    excluded_keywords,
                )
                if ok:
                    added += 1
        except Exception as e:
            print(f"[AF] Error for '{q_cfg['q']}': {e}", flush=True)
    return added


# --- SCRAPER 2: TEAMTAILOR DIRECT RSS FEEDS ---
def fetch_teamtailor_jobs(
    seen_in_batch: set,
    batch_title_pairs: list[tuple[str, str]],
    output_list: list[dict],
    allowed_locations: list[str],
    excluded_keywords: list[str],
) -> int:
    added = 0
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScoutBot/1.0)"}
    for feed in TEAMTAILOR_FEEDS:
        company = feed["company"]
        url = feed["url"]
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item"):
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
                    allowed_locations,
                    excluded_keywords,
                )
                if ok:
                    added += 1
        except Exception as e:
            print(f"[Teamtailor] Error for {company}: {e}", flush=True)
    return added


# --- SCRAPER 3: JOBSPY (INDEED 7-DAY WINDOW) ---
def fetch_jobspy_jobs(
    seen_in_batch: set,
    batch_title_pairs: list[tuple[str, str]],
    output_list: list[dict],
    allowed_locations: list[str],
    excluded_keywords: list[str],
    queries: list[dict],
) -> int:
    added = 0
    for q_cfg in queries:
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
            if df is not None and len(df) > 0:
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
                        allowed_locations,
                        excluded_keywords,
                    )
                    if ok:
                        added += 1
            time.sleep(2)
        except Exception as e:
            print(f"[JobSpy] Warning for {q_cfg['location']}: {str(e)[:120]}", flush=True)
    return added


# --- SCRAPER 4: SERPAPI X-RAY WITH CURSOR ROTATION ---
def run_xray_queries(
    conn: sqlite3.Connection,
    cursor_key: str,
    query_pool: list[dict],
    api_key: str,
    num_queries: int,
    seen_in_batch: set,
    batch_title_pairs: list[tuple[str, str]],
    output_list: list[dict],
    allowed_locations: list[str],
    excluded_keywords: list[str],
    profile_tag: str,
) -> tuple[int, int]:
    if num_queries <= 0 or not api_key or not query_pool:
        return 0, 0

    row = conn.execute("SELECT value FROM meta_state WHERE key = ?", (cursor_key,)).fetchone()
    cursor = row[0] if row else 0

    selected = [query_pool[(cursor + i) % len(query_pool)] for i in range(num_queries)]
    new_cursor = (cursor + num_queries) % len(query_pool)
    conn.execute(
        "INSERT OR REPLACE INTO meta_state (key, value) VALUES (?, ?)",
        (cursor_key, new_cursor),
    )
    conn.commit()

    added = 0
    used = 0
    for q_cfg in selected:
        params = {
            "engine": "google",
            "q": q_cfg["q"],
            "gl": q_cfg["gl"],
            "tbs": "qdr:w",
            "num": 10,
            "api_key": api_key,
        }
        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=20)
            resp.raise_for_status()
            used += 1
            results = resp.json().get("organic_results") or []
            print(f"[SerpApi:{cursor_key}] '{q_cfg['q'][:42]}...': {len(results)} results", flush=True)
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
                    allowed_locations,
                    excluded_keywords,
                )
                if ok:
                    added += 1
                    item = output_list[-1]
                    exists = conn.execute(
                        "SELECT 1 FROM seen_jobs WHERE profile = ? AND job_hash = ?",
                        (profile_tag, item["hash"]),
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
            print(f"[SerpApi:{cursor_key}] Error: {e}", flush=True)
    return added, used


# --- BATCH EVALUATE WITH LOW-THINKING & 4 ACTIVE GEMINI MODELS ---
def evaluate_batch(
    client: genai.Client,
    batch: list[dict],
    system_prompt: str,
    skip_primary: bool,
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
        ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        if skip_primary
        else [
            "gemini-4-hydrogen",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ]
    )
    backoff_seconds = [5, 10, 15, 20]

    for attempt, model_name in enumerate(models_to_try):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=BatchEvaluation,
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                ),
            )
            evals = response.parsed.evaluations if response.parsed else []
            if evals:
                print(f"Batch evaluated ({len(evals)} jobs) via {model_name}", flush=True)
                return evals, skip_primary
        except Exception as e:
            err_str = str(e)
            print(f"Attempt {attempt + 1} ({model_name}) failed: {err_str[:140]}", flush=True)
            is_404 = "404" in err_str or "NOT_FOUND" in err_str
            if model_name == "gemini-4-hydrogen" and ("RESOURCE_EXHAUSTED" in err_str or is_404):
                skip_primary = True
            if is_404:
                continue
            time.sleep(backoff_seconds[min(attempt, len(backoff_seconds) - 1)])
    return [], skip_primary


# --- DISCORD SENDER (ID + TEXT FIRST + RAW COPYABLE LINK BELOW + UNIQUE COLORS) ---
def send_discord_embed(
    webhook_url: str,
    job_id: int,
    eval_data: JobEvaluation,
    job: dict,
    quota_text: str,
    run_date: str,
    today: date,
    is_unique_anthon: bool = False,
):
    if not webhook_url:
        return

    if is_unique_anthon:
        if eval_data.overall_score >= 9:
            color = 0x3498DB  # Blue: Unique + 9-10
            badge = "🔵 UNIK TOP MATCH"
        else:
            color = 0xE67E22  # Orange: Unique + 7-8
            badge = "🟠 UNIK"
    else:
        if eval_data.overall_score >= 9:
            color = 0x2ECC71  # Green: Standard/Shared + 9-10
            badge = "🟢 TOP MATCH"
        else:
            color = 0xF1C40F  # Yellow: Standard/Shared + 7-8
            badge = "🟡"

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
        "title": f"🆔 #{job_id} • {badge} {eval_data.overall_score}/10 — {eval_data.job_title} ({eval_data.company})",
        "description": f"*{eval_data.match_rationale}*",
        "color": color,
        "footer": {"text": f"ID #{job_id} • Source: {source_name} • Searched: {run_date} • {quota_text}"},
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

    header_tag = ""
    if is_unique_anthon:
        header_tag = f"✨ **UNIKT FÖR ANTHONS SÖKNING** (<@{MATIN_DISCORD_USER_ID}>)\n"
    elif eval_data.overall_score >= 9:
        header_tag = f"🔥 **9–10/10 High-Priority Match** (ID `#{job_id}`)\n"

    content_text = f"{header_tag}🔗 **Ansök / Kopiera länk (ID #{job_id}):**\n{apply_url}"

    resp = requests.post(
        webhook_url,
        json={"content": content_text, "embeds": [embed]},
        timeout=15,
    )
    if resp.status_code not in (200, 204):
        print(f"Discord webhook error {resp.status_code}: {resp.text}", flush=True)
    time.sleep(1.5)


def filter_unseen_for_profile(
    conn: sqlite3.Connection,
    profile: str,
    candidates: list[dict],
    recent_cutoff: str,
) -> list[dict]:
    db_rows = conn.execute(
        "SELECT title, company FROM seen_jobs "
        "WHERE profile = ? AND (created_at IS NULL OR created_at = '' OR created_at >= ?)",
        (profile, recent_cutoff),
    ).fetchall()
    db_pairs = [(normalize_token(r[0] or ""), normalize_token(r[1] or "")) for r in db_rows]

    unseen = []
    for job in candidates:
        exists = conn.execute(
            "SELECT 1 FROM seen_jobs WHERE profile = ? AND job_hash = ?",
            (profile, job["hash"]),
        ).fetchone()
        if exists:
            continue
        if is_fuzzy_duplicate(job["title"], job["company_name"], db_pairs):
            continue
        unseen.append(job)
    return unseen


def main():
    conn = init_db()
    now_cet = get_swedish_now()
    today = now_cet.date()
    today_iso = today.strftime("%Y-%m-%d")
    recent_cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    run_date = now_cet.strftime("%Y-%m-%d %H:%M CET")
    client = genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": 90000})

    budgets = compute_dual_budgets()
    print(
        f"[{run_date}] SerpApi Dual Quota -> {budgets['status_summary']} | "
        f"Matin daily: {budgets['matin_daily']} (K1:{budgets['k1_matin']} + K2:{budgets['k2_matin']}), "
        f"Anthon daily: {budgets['anthon_daily']}",
        flush=True,
    )

    skip_primary = False

    # =========================================================================
    # PHASE 1: MATIN'S GENERAL SEARCH (75% NON-STOCKHOLM / 25% STOCKHOLM)
    # =========================================================================
    matin_seen = set()
    matin_pairs = []
    matin_raw = []

    af_m = fetch_af_jobs(
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS, AF_QUERIES
    )
    tt_m = fetch_teamtailor_jobs(
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS
    )
    js_m = fetch_jobspy_jobs(
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS, JOBSPY_QUERIES
    )

    m_total_q = budgets["matin_daily"]
    m_sthlm_q = 1 if m_total_q >= 2 else 0
    if m_total_q >= 4:
        m_sthlm_q = max(1, round(m_total_q * 0.25))
    m_non_sthlm_q = max(0, m_total_q - m_sthlm_q)

    k1_rem = budgets["k1_matin"]
    k2_rem = budgets["k2_matin"]

    use_k1_non = min(k1_rem, m_non_sthlm_q)
    k1_rem -= use_k1_non
    use_k2_non = min(k2_rem, m_non_sthlm_q - use_k1_non)
    k2_rem -= use_k2_non

    s1_add, s1_used = run_xray_queries(
        conn, "matin_non_sthlm_k1", MATIN_XRAY_NON_STHLM, SERPAPI_KEY_1, use_k1_non,
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS, "matin"
    )
    s2_add, s2_used = run_xray_queries(
        conn, "matin_non_sthlm_k2", MATIN_XRAY_NON_STHLM, SERPAPI_KEY_2, use_k2_non,
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS, "matin"
    )

    use_k1_sthlm = min(k1_rem, m_sthlm_q)
    k1_rem -= use_k1_sthlm
    use_k2_sthlm = min(k2_rem, m_sthlm_q - use_k1_sthlm)

    s3_add, s3_used = run_xray_queries(
        conn, "matin_sthlm_k1", MATIN_XRAY_STHLM, SERPAPI_KEY_1, use_k1_sthlm,
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS, "matin"
    )
    s4_add, s4_used = run_xray_queries(
        conn, "matin_sthlm_k2", MATIN_XRAY_STHLM, SERPAPI_KEY_2, use_k2_sthlm,
        matin_seen, matin_pairs, matin_raw, MATIN_ALLOWED_LOCATIONS, MATIN_EXCLUDED_KEYWORDS, "matin"
    )

    matin_serp_add = s1_add + s2_add + s3_add + s4_add
    matin_serp_used = s1_used + s2_used + s3_used + s4_used
    print(
        f"[MATIN] Fetched {len(matin_raw)} unique (AF:{af_m}, TT:{tt_m}, JS:{js_m}, SerpApi:{matin_serp_add})",
        flush=True,
    )

    matin_unseen = filter_unseen_for_profile(conn, "matin", matin_raw, recent_cutoff)[:MAX_JOBS_PER_PROFILE]
    matin_matches = []
    matin_eval_count = 0

    for i in range(0, len(matin_unseen), BATCH_SIZE):
        batch = matin_unseen[i : i + BATCH_SIZE]
        evals, skip_primary = evaluate_batch(client, batch, MATIN_SYSTEM_PROMPT, skip_primary)
        for ev in evals:
            if 0 <= ev.job_index < len(batch):
                job = batch[ev.job_index]
                matin_eval_count += 1
                jid = save_seen_job(conn, "matin", job, ev.overall_score, today_iso)
                print(
                    f"[MATIN #{jid}] [{job['source']}] {job['title']} -> {ev.overall_score}/10 ({ev.fit_verdict})",
                    flush=True,
                )
                if ev.fit_verdict != "SKIP" and ev.overall_score >= 7:
                    matin_matches.append((jid, ev, job))
        time.sleep(2)

    # =========================================================================
    # PHASE 2: ANTHON'S STOCKHOLM SEARCH (SHARED STOCKHOLM + UNIQUE ANTHON)
    # =========================================================================
    anthon_seen = set()
    anthon_pairs = []
    anthon_shared_candidates = []
    anthon_unique_candidates = []

    matin_hashes_all = {j["hash"] for j in matin_raw}
    for job in matin_raw:
        if is_stockholm_job(job):
            register_job(
                dict(job),
                anthon_seen,
                anthon_pairs,
                anthon_shared_candidates,
                ANTHON_ALLOWED_LOCATIONS,
                ANTHON_EXCLUDED_KEYWORDS,
            )

    af_sthlm_queries = [q for q in AF_QUERIES if "Stockholm" in q["q"] or "Södertälje" in q["q"] or q["limit"] == 40][:18]
    js_sthlm_queries = [q for q in JOBSPY_QUERIES if "Stockholm" in q["location"]]

    af_a = fetch_af_jobs(
        anthon_seen, anthon_pairs, anthon_unique_candidates,
        ANTHON_ALLOWED_LOCATIONS, ANTHON_EXCLUDED_KEYWORDS, af_sthlm_queries
    )
    js_a = fetch_jobspy_jobs(
        anthon_seen, anthon_pairs, anthon_unique_candidates,
        ANTHON_ALLOWED_LOCATIONS, ANTHON_EXCLUDED_KEYWORDS, js_sthlm_queries
    )
    serp_a_add, serp_a_used = run_xray_queries(
        conn, "anthon_sthlm_k2", ANTHON_XRAY_STHLM, SERPAPI_KEY_2, budgets["anthon_daily"],
        anthon_seen, anthon_pairs, anthon_unique_candidates,
        ANTHON_ALLOWED_LOCATIONS, ANTHON_EXCLUDED_KEYWORDS, "anthon"
    )

    for j in anthon_shared_candidates:
        j["is_unique_anthon"] = False
    for j in anthon_unique_candidates:
        in_matin_db = conn.execute(
            "SELECT 1 FROM seen_jobs WHERE profile = 'matin' AND job_hash = ?",
            (j["hash"],),
        ).fetchone()
        j["is_unique_anthon"] = (j["hash"] not in matin_hashes_all) and (in_matin_db is None)

    anthon_combined = anthon_shared_candidates + anthon_unique_candidates
    print(
        f"[ANTHON] Stockholm pool: {len(anthon_combined)} "
        f"(Shared from Matin:{len(anthon_shared_candidates)}, Anthon-specific:{len(anthon_unique_candidates)} "
        f"[AF:{af_a}, JS:{js_a}, SerpApi:{serp_a_add}])",
        flush=True,
    )

    anthon_unseen = filter_unseen_for_profile(conn, "anthon", anthon_combined, recent_cutoff)[:MAX_JOBS_PER_PROFILE]
    anthon_matches = []
    anthon_eval_count = 0

    for i in range(0, len(anthon_unseen), BATCH_SIZE):
        batch = anthon_unseen[i : i + BATCH_SIZE]
        evals, skip_primary = evaluate_batch(client, batch, ANTHON_SYSTEM_PROMPT, skip_primary)
        for ev in evals:
            if 0 <= ev.job_index < len(batch):
                job = batch[ev.job_index]
                anthon_eval_count += 1
                jid = save_seen_job(conn, "anthon", job, ev.overall_score, today_iso)
                uniq_flag = "UNIQUE" if job.get("is_unique_anthon") else "SHARED"
                print(
                    f"[ANTHON #{jid}] [{uniq_flag}] [{job['source']}] {job['title']} -> "
                    f"{ev.overall_score}/10 ({ev.fit_verdict})",
                    flush=True,
                )
                if ev.fit_verdict != "SKIP" and ev.overall_score >= 7:
                    anthon_matches.append((jid, ev, job))
        time.sleep(2)

    # =========================================================================
    # PHASE 3: DELIVER TO MATIN'S & ANTHON'S DISCORD CHANNELS
    # =========================================================================
    updated_budgets = compute_dual_budgets()
    quota_footer = updated_budgets["status_summary"]

    matin_matches.sort(key=lambda x: x[1].overall_score, reverse=True)
    for jid, ev, job_item in matin_matches[:25]:
        send_discord_embed(
            DISCORD_WEBHOOK_URL_MATIN, jid, ev, job_item, quota_footer, run_date, today,
            is_unique_anthon=False,
        )

    matin_summary = (
        f"📊 **Matin Daily Job Scout ({run_date}):** Evaluated **{matin_eval_count}** new jobs "
        f"(AF:{af_m}, TT:{tt_m}, JS:{js_m}, SerpApi:{matin_serp_add} [used {matin_serp_used}q: 75% Övrigt / 25% Sthlm]) • "
        f"Found **{len(matin_matches)}** matches (7+) • {quota_footer}"
    )
    requests.post(DISCORD_WEBHOOK_URL_MATIN, json={"content": matin_summary}, timeout=15)

    if DISCORD_WEBHOOK_URL_ANTHON:
        anthon_matches.sort(key=lambda x: x[1].overall_score, reverse=True)
        unique_hits = sum(1 for _, _, j in anthon_matches if j.get("is_unique_anthon"))
        for jid, ev, job_item in anthon_matches[:25]:
            send_discord_embed(
                DISCORD_WEBHOOK_URL_ANTHON, jid, ev, job_item, quota_footer, run_date, today,
                is_unique_anthon=bool(job_item.get("is_unique_anthon")),
            )

        anthon_summary = (
            f"📊 **Anthon Stockholm Job Scout ({run_date}):** Evaluated **{anthon_eval_count}** Stockholm jobs "
            f"(Delade:{len(anthon_shared_candidates)}, Unika sök:{len(anthon_unique_candidates)}, SerpApi used:{serp_a_used}q) • "
            f"Found **{len(anthon_matches)}** matches (7+, varav **{unique_hits}** helt unika) • {quota_footer}"
        )
        requests.post(DISCORD_WEBHOOK_URL_ANTHON, json={"content": anthon_summary}, timeout=15)

    print(
        f"Done. Matin sent {min(len(matin_matches), 25)} | Anthon sent {min(len(anthon_matches), 25)}.",
        flush=True,
    )


if __name__ == "__main__":
    main()
