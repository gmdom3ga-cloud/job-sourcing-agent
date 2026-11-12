import os
import sqlite3
import hashlib
import time
from typing import Literal, List
import requests
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
DB_PATH = "seen_jobs.db"
AF_API_URL = "https://jobsearch.api.jobtechdev.se/search"

# Targeted Stockholm queries on Arbetsförmedlingen (Platsbanken)
STOCKHOLM_QUERIES = [
    {"q": "simuleringsingenjör Stockholm", "limit": 15},
    {"q": "produktionsingenjör Stockholm", "limit": 15},
    {"q": "simulation engineer Stockholm", "limit": 15},
    {"q": "production engineer Stockholm", "limit": 15},
]

# --- PYDANTIC SCHEMA FOR STRUCTURED OUTPUT ---
class RequirementItem(BaseModel):
    required: str
    fit_rating: Literal["Strong Match", "Partial Match", "Stretch", "Gap"]
    note: str

class SkillItem(BaseModel):
    skill: str
    fit_rating: Literal["Strong", "Moderate", "None"]

class TraitItem(BaseModel):
    trait: str
    proven_record_required: bool
    fit_rating: Literal["Strong", "Moderate", "N/A"]

class RequirementsBreakdown(BaseModel):
    education: RequirementItem
    experience: RequirementItem
    skills_mandatory: List[SkillItem]
    skills_meritorious: List[SkillItem]
    personal_traits: List[TraitItem]

class JobEvaluation(BaseModel):
    job_title: str
    company: str
    location: str
    region_tier: Literal["Primary (Sweden/Copenhagen)", "Oslo (High-Fit Exception)", "Outside Target"]
    work_model: Literal["On-site", "Hybrid", "Remote", "Not specified"]
    language_required: str
    amount_of_applicants: str
    company_vision_values: str
    requirements_breakdown: RequirementsBreakdown
    overall_score: int
    fit_verdict: Literal["APPLY", "CONSIDER", "SKIP"]
    match_rationale: str

SYSTEM_PROMPT = """
You are an expert technical recruiter and talent evaluator in Scandinavia. Evaluate the given job description against the candidate's profile, break down requirements, rate fit, and assign an overall relevance score (1-10).

### CANDIDATE PROFILE:
- Name: Matin Ahmadi
- Degrees: Civilingenjör (M.Sc.) in Industrial Management & Engineering (300 ECTS) & M.Sc. in Mechanical Engineering (120 ECTS, dual degree) from Örebro University; B.Sc. Business Administration coursework complete; Exchange at BI Norwegian Business School (Oslo).
- Core Skills: Discrete-event simulation (Siemens Tecnomatix Plant Simulation / SimTalk), multi-domain dynamic simulation (Hopsan / TLM), MATLAB & Simulink, FMU/FMI, HIL, C++, Python, CAD (PTC Creo, SolidWorks, AutoCAD), Lean Six Sigma, Kaizen/PDCA, VSM, FMEA, SAP ERP, Power BI, GIS.
- Experience:
  * Epiroc Rock Drills AB (Master Thesis, Top Grade 5): Real-time hydromechanical dynamic simulation in Hopsan & 7 modular C++ components (7.6x faster than real-time), empirical test-bench validation.
  * AstraZeneca (Degree Project): Discrete-event digital twin in Tecnomatix Plant Simulation (4,100+ lab tests), hybrid scheduling reducing failure rate by 56% and delay by 61.3%.
  * Örebro University (Teaching Assistant, 2022–2026): Mechanics, structural mechanics, applied math, CAD.
  * Malmö stad (Project Assistant & Analyst, 2023–2024): Financial modeling in Excel; supervised teams of 15–19 interns; GIS asset management.
  * AkzoNobel & VA SYD (2021–2022): SAP ERP inventory management, forklift operation, GIS mapping.
- Languages: Swedish (Native), English (Fluent), Pashto/Dari (Fluent), Spanish (Basic).

### GEOGRAPHIC & DOMAIN RULES:
1. Primary Regions: Malmö, Örebro, Stockholm, and ~1h commute (Lund, Copenhagen, Södertälje, Karlskoga, Eskilstuna, Västerås, Helsingborg).
2. Oslo Exception: Roles in Oslo are eligible ONLY if overall_score is 9 or 10. Otherwise set fit_verdict to "SKIP".
3. Defense Exclusion: Disqualify immediately (overall_score: 1, fit_verdict: "SKIP") if the company or role involves the defense, weapons, armament, or military industry.

### TRAIT & SCORING RULES:
- Personal Traits: List requested traits. ONLY rate fit if the ad asks for a *proven track record* or documented experience of that trait. Otherwise set proven_record_required: false and fit_rating: "N/A".
- 9-10: Graduate/entry-level/trainee (0-2 yrs exp); matches IEM or Mechanical Engineering; high overlap with simulation, Lean, or operations tools.
- 7-8: Strong technical match with candidate's projects/skills but asks for 2-3+ yrs exp, OR solid general engineering/analyst role matching core competencies.
- 5-6: Moderate overlap, requires 3-5 yrs specialized experience outside candidate's background.
- 1-4: Irrelevant field, 7+ yrs senior requirement, outside target locations, or defense sector.
"""

# --- DATABASE FUNCTIONS ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen_jobs (job_hash TEXT PRIMARY KEY, title TEXT, company TEXT, score INTEGER)")
    conn.commit()
    return conn

def get_job_hash(title: str, company: str, location: str) -> str:
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()

# --- FETCH, EVALUATE & NOTIFY ---
def fetch_jobs():
    all_jobs = []
    headers = {"Accept": "application/json"}
    for q_cfg in STOCKHOLM_QUERIES:
        params = {
            "q": q_cfg["q"],
            "limit": q_cfg["limit"],
            "sort": "pubdate-desc",
        }
        try:
            resp = requests.get(AF_API_URL, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", [])
            print(f"Query '{q_cfg['q']}': found {len(hits)} jobs")
            for hit in hits:
                all_jobs.append({
                    "title": hit.get("headline", ""),
                    "company_name": (hit.get("employer") or {}).get("name", "Unknown"),
                    "location": (hit.get("workplace_address") or {}).get("municipality", "Stockholm"),
                    "description": (hit.get("description") or {}).get("text", ""),
                    "apply_url": hit.get("webpage_url", "https://arbetsformedlingen.se/platsbanken"),
                    "employment_type": (hit.get("working_hours_type") or {}).get("label", ""),
                })
        except Exception as e:
            print(f"Arbetsförmedlingen search error for '{q_cfg['q']}': {e}")
    return all_jobs

def evaluate_job(client: genai.Client, job: dict) -> JobEvaluation | None:
    job_text = (
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company_name')}\n"
        f"Location: {job.get('location')}\n"
        f"Employment Type: {job.get('employment_type')}\n"
        f"Description:\n{job.get('description', '')}"
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=job_text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=JobEvaluation,
                temperature=0.2,
            ),
        )
        return response.parsed
    except Exception as e:
        print(f"Gemini evaluation error for '{job.get('title')}': {e}")
        return None

def format_skills(skills: list[SkillItem], limit: int = 5) -> str:
    if not skills:
        return "None specified"
    icons = {"Strong": "✅", "Moderate": "⚠️", "None": "❌"}
    shown = [f"{icons.get(s.fit_rating, '•')} {s.skill}" for s in skills[:limit]]
    extra = len(skills) - limit
    if extra > 0:
        shown.append(f"*(+{extra} more)*")
    return " • ".join(shown)

def send_discord_embed(eval_data: JobEvaluation, apply_url: str):
    color = 0x2ECC71 if eval_data.overall_score >= 9 else 0xF1C40F
    badge = "🟢" if eval_data.overall_score >= 9 else "🟡"

    proven_traits = [t for t in eval_data.requirements_breakdown.personal_traits if t.proven_record_required]
    traits_text = (
        " • ".join([f"{'✅' if t.fit_rating == 'Strong' else '⚠️'} {t.trait}" for t in proven_traits])
        if proven_traits
        else "No proven track record required"
    )

    embed = {
        "title": f"{badge} {eval_data.overall_score}/10 — {eval_data.job_title} ({eval_data.company})",
        "url": apply_url,
        "description": f"*{eval_data.match_rationale}*\n\n**🏛️ Vision & Culture:** {eval_data.company_vision_values[:250]}",
        "color": color,
        "fields": [
            {"name": "📍 Location", "value": eval_data.location, "inline": True},
            {"name": "🏢 Model", "value": eval_data.work_model, "inline": True},
            {"name": "🗣️ Language", "value": eval_data.language_required, "inline": True},
            {"name": "👥 Applicants", "value": eval_data.amount_of_applicants, "inline": True},
            {"name": "🗺️ Region Tier", "value": eval_data.region_tier, "inline": True},
            {
                "name": "🎓 Education & Experience",
                "value": (
                    f"• **Edu:** {eval_data.requirements_breakdown.education.fit_rating} ({eval_data.requirements_breakdown.education.required})\n"
                    f"• **Exp:** {eval_data.requirements_breakdown.experience.fit_rating} ({eval_data.requirements_breakdown.experience.required})"
                ),
                "inline": False,
            },
            {
                "name": "🛠️ Mandatory Skills (Top 5)",
                "value": format_skills(eval_data.requirements_breakdown.skills_mandatory)[:1024],
                "inline": False,
            },
            {
                "name": "⭐ Meritorious Skills (Top 5)",
                "value": format_skills(eval_data.requirements_breakdown.skills_meritorious)[:1024],
                "inline": False,
            },
            {
                "name": "🤝 Proven Personal Traits",
                "value": traits_text[:1024],
                "inline": False,
            },
        ],
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
    if resp.status_code not in (200, 204):
        print(f"Discord webhook error {resp.status_code}: {resp.text}")
    time.sleep(1.5)

def main():
    conn = init_db()
    client = genai.Client(api_key=GEMINI_API_KEY)
    raw_jobs = fetch_jobs()
    print(f"Fetched {len(raw_jobs)} raw jobs.")

    matches = []
    for job in raw_jobs:
        title = job.get("title", "")
        company = job.get("company_name", "")
        location = job.get("location", "")
        job_hash = get_job_hash(title, company, location)

        if conn.execute("SELECT 1 FROM seen_jobs WHERE job_hash = ?", (job_hash,)).fetchone():
            continue

        evaluation = evaluate_job(client, job)
        if not evaluation:
            continue

        conn.execute(
            "INSERT INTO seen_jobs (job_hash, title, company, score) VALUES (?, ?, ?, ?)",
            (job_hash, title, company, evaluation.overall_score),
        )
        conn.commit()

        if evaluation.fit_verdict != "SKIP" and evaluation.overall_score >= 7:
            matches.append((evaluation, job.get("apply_url", "https://arbetsformedlingen.se/platsbanken")))

    matches.sort(key=lambda x: x[0].overall_score, reverse=True)
    for eval_data, url in matches[:20]:
        send_discord_embed(eval_data, url)

    print(f"Sent {min(len(matches), 20)} matching jobs to Discord.")

if __name__ == "__main__":
    main()
    
