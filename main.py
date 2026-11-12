import os
import json
import re
import calendar
import sqlite3
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Literal, List
import requests
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
DB_PATH = "seen_jobs.db"
AF_API_URL = "https://jobsearch.api.jobtechdev.se/search"
SERPAPI_URL = "https://serpapi.com/search.json"
BATCH_SIZE = 10
MAX_JOBS_PER_RUN = 60
SERPAPI_TEST_RESERVE_RATIO = 0.15  # Reserve 15% of monthly searches for manual testing

# 1. Unlimited & Free: Arbetsförmedlingen (Platsbanken) - Broad + Specific across Target Regions
AF_QUERIES = [
    {"q": "industriell ekonomi", "limit": 20},
    {"q": "maskinteknik", "limit": 20},
    {"q": "civilingenjör trainee", "limit": 15},
    {"q": "graduate engineer", "limit": 15},
    {"q": "ingenjör Stockholm", "limit": 15},
    {"q": "engineer Stockholm", "limit": 15},
    {"q": "ingenjör Örebro", "limit": 15},
    {"q": "ingenjör Malmö", "limit": 15},
    {"q": "ingenjör Lund", "limit": 10},
    {"q": "ingenjör Södertälje", "limit": 10},
    {"q": "ingenjör Västerås", "limit": 10},
    {"q": "simuleringsingenjör", "limit": 15},
    {"q": "simulation engineer", "limit": 15},
    {"q": "produktionsingenjör", "limit": 15},
    {"q": "production engineer", "limit": 15},
    {"q": "processingenjör", "limit": 15},
    {"q": "lean six sigma", "limit": 10},
    {"q": "supply chain analyst", "limit": 10},
    {"q": "verksamhetsutvecklare", "limit": 10},
]

# 2. Quota-Paced: SerpApi Queries (Copenhagen, Oslo & Nordic Direct Career Portals)
SERPAPI_QUERIES = [
    {"q": 'site:linkedin.com/jobs ("simulation engineer" OR "production engineer" OR "graduate engineer") Copenhagen', "location": "Copenhagen"},
    {"q": 'site:jobindex.dk ("simulation engineer" OR "production engineer" OR "industrial engineer")', "location": "Copenhagen"},
    {"q": 'site:linkedin.com/jobs ("simulation engineer" OR "production engineer" OR "graduate engineer") Oslo', "location": "Oslo"},
    {"q": 'site:finn.no/job ("simuleringsingeniør" OR "produksjonsingeniør" OR "sivilingeniør" OR "graduate") Oslo', "location": "Oslo"},
    {"q": 'site:teamtailor.com ("simulation engineer" OR "simuleringsingenjör" OR "produktionsingenjör") (Stockholm OR Malmö OR Örebro)', "location": "Sweden"},
    {"q": 'site:linkedin.com/jobs ("industrial management" OR "industriell ekonomi" OR "operations analyst") (Stockholm OR Malmö OR Copenhagen)', "location": "Nordics"},
    {"q": 'site:linkedin.com/jobs ("lean engineer" OR "continuous improvement" OR "process engineer") (Stockholm OR Malmö OR Örebro)', "location": "Sweden"},
    {"q": 'site:linkedin.com/jobs ("mechanical engineer" OR "maskiningenjör") (Örebro OR Stockholm OR Malmö)', "location": "Sweden"},
]

EXCLUDED_TITLE_KEYWORDS = [
    "byggkonstruktör",
    "elkraft",
    "högspänning",
    "high-voltage",
    "hvdc",
    "telecommunications",
    "el och automation",
    "bim-specialist",
    "kärnkraft",
    "försvarsmakten",
    "saab",
    "bae systems",
    "fmv",
    "sjuksköterska",
    "läkare",
    "lärare",
    "undersköterska",
    "personlig assistent",
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
    "regulatory reporting",
    "financial controlling",
    "redovisningsekonom",
]


# --- LEAN, KEYWORD-ONLY PYDANTIC SCHEMA ---
class JobEvaluation(BaseModel):
    job_index: int
    job_title: str
    company: str
    location: str
    work_model: Literal["On-site", "Hybrid", "Remote", "Not specified"] = "Not specified"
    language_required: str = "Not specified"
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
- Primary Regions: Malmö, Örebro, Stockholm, and ~1h commute (Lund, Copenhagen, Södertälje, Karlskoga, Eskilstuna, Västerås, Helsingborg). Roles in other Swedish cities (e.g. Gothenburg) should be scored <= 5 (`fit_verdict`: "SKIP") unless remote/hybrid from target regions.
- Oslo Exception: Roles in Oslo are eligible ONLY if `overall_score` is 9 or 10; otherwise set `fit_verdict` to "SKIP".
- Defense Exclusion: Disqualify immediately (`overall_score`: 1, `fit_verdict`: "SKIP") if the company or role involves defense, weapons, armament, or military.
- 9-10: Graduate/entry-level/trainee (0-2 yrs exp) matching IEM or Mechanical Engineering, simulation, Lean, or operations in target regions.
- 7-8: Strong technical match with candidate's tools/projects (2-3+ yrs exp) or solid general engineering/analyst role in Primary Regions.
- 1-6: Weak overlap, 5+ yrs senior role, outside target locations, or defense sector (`fit_verdict`: "SKIP").
"""


# --- DATABASE & QUOTA FUNCTIONS ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_jobs "
        "(job_hash TEXT PRIMARY KEY, title TEXT, company TEXT, score INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta_state "
        "(key TEXT PRIMARY KEY, value INTEGER)"
    )
    conn.commit()
    return conn


def get_job_hash(title: str, company: str, location: str) -> str:
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_swedish_now() -> datetime:
    cet = timezone(timedelta(hours=1))
    return datetime.now(cet)


def get_swedish_timestamp() -> str:
    return get_swedish_now().strftime("%Y-%m-%d %H:%M CET")


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

        if spendable <= 0:
            daily_budget = 0
        else:
            daily_budget = max(1, spendable // days_left)

        status = f"{left}/{total} left (15% test reserve: {reserve}, today's auto-budget: {daily_budget})"
        return daily_budget, status
    except Exception as e:
        print(f"SerpApi quota check error: {e}", flush=True)
        return 0, "Quota unavailable"


# --- FETCH FROM ARBETSFÖRMEDLINGEN + PACED SERPAPI ---
def fetch_af_jobs(seen_in_batch: set) -> list[dict]:
    jobs = []
    headers = {"Accept": "application/json"}
    for q_cfg in AF_QUERIES:
        params = {
            "q": q_cfg["q"],
            "limit": q_cfg["limit"],
            "sort": "pubdate-desc",
        }
        try:
            resp = requests.get(AF_API_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits") or []
            print(f"[AF] '{q_cfg['q']}': {len(hits)} hits", flush=True)
            for hit in hits:
                title = hit.get("headline") or ""
                if not title:
                    continue
                company = (hit.get("employer") or {}).get("name") or "Unknown"
                location = (hit.get("workplace_address") or {}).get("municipality") or "Sweden"

                title_lower = title.lower()
                if any(bad in title_lower for bad in EXCLUDED_TITLE_KEYWORDS):
                    continue

                j_hash = get_job_hash(title, company, location)
                if j_hash in seen_in_batch:
                    continue
                seen_in_batch.add(j_hash)

                desc_text = (hit.get("description") or {}).get("text") or ""
                apply_url = hit.get("webpage_url") or "https://arbetsformedlingen.se/platsbanken"

                jobs.append(
                    {
                        "hash": j_hash,
                        "source": "Platsbanken",
                        "title": title,
                        "company_name": company,
                        "location": location,
                        "description": desc_text[:2800],
                        "apply_url": apply_url,
                    }
                )
        except Exception as e:
            print(f"[AF] Error for '{q_cfg['q']}': {e}", flush=True)
    return jobs


def fetch_serpapi_jobs(conn: sqlite3.Connection, daily_budget: int, seen_in_batch: set) -> tuple[list[dict], int]:
    if daily_budget <= 0 or not SERPAPI_KEY:
        print(f"[SerpApi] Skipping automated search (daily_budget={daily_budget}).", flush=True)
        return [], 0

    row = conn.execute("SELECT value FROM meta_state WHERE key = 'serpapi_cursor'").fetchone()
    cursor = row[0] if row else 0

    num_queries = min(daily_budget, len(SERPAPI_QUERIES))
    selected_queries = [
        SERPAPI_QUERIES[(cursor + i) % len(SERPAPI_QUERIES)]
        for i in range(num_queries)
    ]

    new_cursor = (cursor + num_queries) % len(SERPAPI_QUERIES)
    conn.execute(
        "INSERT OR REPLACE INTO meta_state (key, value) VALUES ('serpapi_cursor', ?)",
        (new_cursor,),
    )
    conn.commit()

    jobs = []
    used = 0
    for q_cfg in selected_queries:
        params = {
            "engine": "google",
            "q": q_cfg["q"],
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
                if not title or not link:
                    continue

                title_lower = title.lower()
                if any(bad in title_lower for bad in EXCLUDED_TITLE_KEYWORDS):
                    continue

                displayed = res.get("displayed_link") or "Web"
                company = displayed.split("/")[0]
                location = q_cfg["location"]
                j_hash = get_job_hash(title, company, location)
                if j_hash in seen_in_batch:
                    continue
                seen_in_batch.add(j_hash)

                jobs.append(
                    {
                        "hash": j_hash,
                        "source": "SerpApi",
                        "title": title,
                        "company_name": company,
                        "location": location,
                        "description": f"Source URL: {link}\nSnippet: {snippet}"[:2800],
                        "apply_url": link,
                    }
                )
        except Exception as e:
            print(f"[SerpApi] Error for query: {e}", flush=True)
    return jobs, used


# --- BATCH EVALUATE WITH GEMMA-3-27B FALLBACK ---
def parse_gemma_json(raw_text: str) -> list[JobEvaluation]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
    data = json.loads(cleaned)
    items = data.get("evaluations", data) if isinstance(data, dict) else data
    return [JobEvaluation(**item) for item in items]


def evaluate_batch(client: genai.Client, batch: list[dict], skip_primary: bool) -> tuple[list[JobEvaluation], bool]:
    blocks = []
    for idx, job in enumerate(batch):
        block = (
            f"=== JOB_INDEX: {idx} ===\n"
            f"Source: {job.get('source')}\n"
            f"Title: {job.get('title')}\n"
            f"Company: {job.get('company_name')}\n"
            f"Location: {job.get('location')}\n"
            f"Description:\n{job.get('description', '')}"
        )
        blocks.append(block)
    prompt_text = "\n\n".join(blocks)

    models_to_try = (
        ["gemini-3.5-flash-lite", "gemma-3-27b-it"]
        if skip_primary
        else ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemma-3-27b-it"]
    )

    for attempt, model_name in enumerate(models_to_try):
        try:
            if model_name.startswith("gemma"):
                gemma_prompt = (
                    f"{SYSTEM_PROMPT}\n\n"
                    'Respond ONLY with valid JSON matching: {"evaluations": [{"job_index": 0, '
                    '"job_title": "...", "company": "...", "location": "...", '
                    '"work_model": "On-site|Hybrid|Remote|Not specified", '
                    '"language_required": "...", "matching_skills": ["..."], '
                    '"gaps_or_requirements": ["..."], "overall_score": 7, '
                    '"fit_verdict": "APPLY|CONSIDER|SKIP", "match_rationale": "..."}]}\n\n'
                    f"{prompt_text}"
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=gemma_prompt,
                    config=types.GenerateContentConfig(temperature=0.2),
                )
                evals = parse_gemma_json(response.text or "")
            else:
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
            time.sleep(3)
    return [], skip_primary


def send_discord_embed(eval_data: JobEvaluation, apply_url: str, quota_text: str, run_date: str):
    color = 0x2ECC71 if eval_data.overall_score >= 9 else 0xF1C40F
    badge = "🟢" if eval_data.overall_score >= 9 else "🟡"

    skills_list = eval_data.matching_skills[:4]
    skills_text = " • ".join([f"✅ {s}" for s in skills_list]) if skills_list else "Engineering Core"

    gaps_list = [g for g in eval_data.gaps_or_requirements[:2] if g.lower() != "none"]
    gaps_text = " • ".join([f"⚠️ {g}" for g in gaps_list]) if gaps_list else "None"

    embed = {
        "title": f"{badge} {eval_data.overall_score}/10 — {eval_data.job_title} ({eval_data.company})",
        "url": apply_url,
        "description": f"*{eval_data.match_rationale}*",
        "color": color,
        "footer": {"text": f"Searched: {run_date} • SerpApi: {quota_text}"},
        "fields": [
            {"name": "📍 Location", "value": eval_data.location, "inline": True},
            {"name": "🏢 Model", "value": eval_data.work_model, "inline": True},
            {"name": "🗣️ Language", "value": eval_data.language_required, "inline": True},
            {"name": "🛠️ Key Matches", "value": skills_text[:1024], "inline": False},
            {"name": "📋 Watchouts / Req", "value": gaps_text[:1024], "inline": False},
        ],
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
    if resp.status_code not in (200, 204):
        print(f"Discord webhook error {resp.status_code}: {resp.text}", flush=True)
    time.sleep(1.5)


def main():
    conn = init_db()
    run_date = get_swedish_timestamp()
    client = genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": 60000})

    daily_budget, budget_status = get_serpapi_budget()
    print(f"[{run_date}] SerpApi budget check -> {budget_status}", flush=True)

    seen_in_batch = set()
    af_jobs = fetch_af_jobs(seen_in_batch)
    serp_jobs, serp_used = fetch_serpapi_jobs(conn, daily_budget, seen_in_batch)
    raw_jobs = af_jobs + serp_jobs
    print(f"Total unique fetched: {len(raw_jobs)} (AF: {len(af_jobs)}, SerpApi: {len(serp_jobs)})", flush=True)

    all_unseen = [
        job
        for job in raw_jobs
        if not conn.execute("SELECT 1 FROM seen_jobs WHERE job_hash = ?", (job["hash"],)).fetchone()
    ]
    unseen_jobs = all_unseen[:MAX_JOBS_PER_RUN]
    print(f"Unseen jobs total: {len(all_unseen)} (evaluating up to {len(unseen_jobs)} this run)", flush=True)

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
                    f"[{evaluated_count}] [{job['source']}] {job['title']} -> {ev.overall_score}/10 ({ev.fit_verdict})",
                    flush=True,
                )
                conn.execute(
                    "INSERT OR IGNORE INTO seen_jobs (job_hash, title, company, score) "
                    "VALUES (?, ?, ?, ?)",
                    (job["hash"], job["title"], job["company_name"], ev.overall_score),
                )
                conn.commit()

                if ev.fit_verdict != "SKIP" and ev.overall_score >= 7:
                    default_url = "https://arbetsformedlingen.se/platsbanken"
                    matches.append((ev, job.get("apply_url", default_url)))
        time.sleep(3)

    _, updated_quota_status = get_serpapi_budget()
    matches.sort(key=lambda x: x[0].overall_score, reverse=True)
    for eval_data, url in matches[:20]:
        send_discord_embed(eval_data, url, updated_quota_status, run_date)

    summary_text = (
        f"📊 **Daily Job Scout ({run_date}):** Evaluated **{evaluated_count}** new jobs "
        f"(AF: {len(af_jobs)}, SerpApi: {len(serp_jobs)}) • Found **{len(matches)}** matches (7+) • "
        f"SerpApi: **{updated_quota_status}** (used {serp_used} today)"
    )
    requests.post(DISCORD_WEBHOOK_URL, json={"content": summary_text}, timeout=15)
    print(f"Done. Sent {min(len(matches), 20)} matching jobs to Discord.", flush=True)


if __name__ == "__main__":
    main()
