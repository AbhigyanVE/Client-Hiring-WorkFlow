"""Virtual Employee Hiring Workspace: generate a JD and rank bench profiles."""

import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import truststore

truststore.inject_into_ssl()

import requests
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from modules.ai_pipeline import AIPipeline
from modules.clients.bench_api import BenchAPIClient
from modules.clients.requirements_api import RequirementsAPIClient
from modules.config import config


app = FastAPI(title="Virtual Employee | Hiring Workspace")
logger = logging.getLogger(__name__)
INDEX_FILE = Path(__file__).resolve().parent / "static" / "index.html"

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect x="3" y="3" width="58" height="58" rx="15" fill="#00bfa5"/>
<path d="M14 22l18 25 18-25" fill="none" stroke="#fff" stroke-width="5"
stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="32" cy="24" r="5" fill="#fff"/>
</svg>"""


class JDRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=4000)


@lru_cache(maxsize=1)
def requirements_client() -> RequirementsAPIClient:
    return RequirementsAPIClient()


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_FILE, media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(FAVICON, media_type="image/svg+xml")


def fetch_bench():
    return BenchAPIClient().fetch_all_bench_resources(raise_on_error=True)

# def fetch_bench():
#     """Fetch all reported pages, or raise if the result can't be verified."""
#     if not config.bench_api_key:
#         raise RuntimeError("VE_BENCH_API_KEY is not configured")

#     limit = 50
#     offset = 0
#     total = None
#     profiles = {}

#     with requests.Session() as session:
#         for _ in range(40):
#             response = session.get(
#                 config.bench_api_url,
#                 headers={"x-api-key": config.bench_api_key},
#                 params={"limit": limit, "offset": offset},
#                 timeout=(4, 8),
#                 allow_redirects=False,
#             )
#             if response.status_code != 200:
#                 raise RuntimeError(f"Bench API returned HTTP {response.status_code}")

#             payload = response.json()
#             batch = payload["data"]
#             page = payload["pagination"]
#             reported_total = int(page["totalRecords"])

#             if (
#                 not isinstance(batch, list)
#                 or reported_total < 0
#                 or int(page["offset"]) != offset
#                 or (total is not None and reported_total != total)
#             ):
#                 raise RuntimeError("Unexpected bench pagination")

#             total = reported_total
#             if (not batch and offset < total) or offset + len(batch) > total:
#                 raise RuntimeError("Incomplete or inconsistent bench page")

#             for row in batch:
#                 profile_id = str(row.get("profile_id") or "").strip()
#                 if not profile_id or profile_id in profiles:
#                     raise RuntimeError("Bench pagination returned missing or duplicate IDs")
#                 profiles[profile_id] = row

#             offset += len(batch)
#             if offset == total:
#                 return list(profiles.values())

#     raise RuntimeError("Bench pagination exceeded the page limit")


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def skill_labels(value):
    """The sample nests skill lists under category, role and skill group."""
    if isinstance(value, dict):
        for child in value.values():
            yield from skill_labels(child)
    elif isinstance(value, list):
        for child in value:
            yield from skill_labels(child)
    elif isinstance(value, str) and value.strip():
        yield value.strip()


def unique_skills(values):
    found = {}
    for value in values:
        value = value.strip()
        if value:
            found.setdefault(value.casefold(), value)
    return list(found.values())


def has_skill(skill, haystack):
    # Avoid substring matches such as Java inside JavaScript.
    pattern = rf"(?<![\w+#]){re.escape(skill.casefold())}(?![\w+#])"
    return bool(re.search(pattern, haystack))


def profile_image(row):
    images = row.get("images") or []
    ordered = sorted(
        (img for img in images if isinstance(img, dict)),
        key=lambda img: not img.get("primary"),
    )
    urls = [row.get("image")]
    for img in ordered:
        urls.extend((img.get("thumbnail"), img.get("url")))

    for url in urls:
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme == "https"
                and parsed.hostname == "nmprofiles.s3.amazonaws.com"
                and not parsed.username
                and not parsed.password
            ):
                return url
        except ValueError:
            continue
    return None


def rank_candidates(parsed, jd, rows):
    # Client-stated technologies are a gate. Generated tags help rank, but
    # aren't allowed to override the client's stated technologies.
    explicit = unique_skills(parsed.explicit_tech_stack)
    draft_skills = unique_skills(jd.required_skills)
    suggested = unique_skills(parsed.inferred_tech_stack + jd.inferred_skills)
    ranked = []

    for row in rows:
        hours = number(row.get("bench_hours"))
        if hours is None or hours <= 0 or not str(row.get("name") or "").strip():
            continue

        years = number(row.get("total_experience_years"))
        tier = parsed.budget_tier
        if tier and years is None:
            continue
        if tier == "low" and years > config.budget_low_max_yoe:
            continue
        if tier == "mid" and not config.budget_low_max_yoe < years <= config.budget_mid_max_yoe:
            continue
        if tier == "high" and years <= config.budget_mid_max_yoe:
            continue

        searchable = [
            str(row.get("skills_search") or ""),
            *skill_labels(row.get("skills_json")),
        ]
        haystack = "\n".join(searchable).casefold()
        explicit_hits = [skill for skill in explicit if has_skill(skill, haystack)]
        if len(explicit_hits) != len(explicit):
            continue

        draft_hits = [skill for skill in draft_skills if has_skill(skill, haystack)]
        if not explicit and not draft_hits:
            continue

        optional_hits = [skill for skill in suggested if has_skill(skill, haystack)]
        matched = unique_skills(explicit_hits + draft_hits + optional_hits)
        availability = str(row.get("availability_status") or "").strip()
        verified = row.get("is_profile_verified") in (1, "1", "true", "True")

        card = {
            "id": str(row["profile_id"]),
            "name": str(row["name"]).strip(),
            "designation": row.get("designation") or None,
            "image": profile_image(row),
            "experience_years": years,
            "bench_hours": hours,
            "availability": availability or None,
            "profile_verified": verified,
            "matched_skills": matched[:4],
        }

        # Count each matching skill once, at its highest weight.
        weights = {}
        for hits, weight in ((optional_hits, 4), (draft_hits, 10), (explicit_hits, 20)):
            for skill in hits:
                weights[skill.casefold()] = weight
        category_match = (
            str(row.get("skill_category_name") or "").casefold()
            == jd.service_category.casefold()
        )
        score = sum(weights.values()) + (3 if category_match else 0)
        ranked.append((score, hours, years or 0, card))

    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["id"]))
    return [item[3] for item in ranked[:3]]


@app.post("/api/jd")
def generate_jd(body: JDRequest, response: Response):
    requirement = body.requirement.strip()
    if not requirement:
        raise HTTPException(422, "Enter a role or project requirement.")

    try:
        client = requirements_client()
        ai = AIPipeline()
        parsed = ai.parse_client_query(requirement, client.get_skill_names())
    except Exception:
        logger.exception("Could not parse hiring requirements")
        raise HTTPException(502, "Couldn’t process your requirements. Please try again.")

    if not parsed.is_valid_ve_request:
        raise HTTPException(
            422, parsed.rejection_message or "Describe a remote role or project."
        )

    try:
        references = client.fetch_reference_requirements(
            keywords=parsed.search_keywords,
            skill=parsed.canonical_skill_filter,
        )
        jd = ai.generate_humanized_jd(parsed, references)
    except Exception:
        logger.exception("Could not generate job description")
        raise HTTPException(502, "Couldn’t generate your job description. Please try again.")

    try:
        matches = rank_candidates(parsed, jd, fetch_bench())
        match_status = "ready" if matches else "no_matches"
    except Exception as exc:
        upstream_response = getattr(exc, "response", None)
        if upstream_response is not None:
            reason = f"upstream HTTP {upstream_response.status_code}"
        elif isinstance(exc, RuntimeError):
            reason = str(exc)
        else:
            reason = type(exc).__name__

        logger.warning("Bench lookup could not be completed: %s", reason)
        matches, match_status = [], "unavailable"

    response.headers["Cache-Control"] = "no-store"
    return {
        "jd": jd.model_dump(
            mode="json",
            exclude={
                "grounded_reference_count": True,
                "timeline": {"estimated_core_weeks", "safety_buffer_weeks"},
            },
        ),
        "matches": matches,
        "match_status": match_status,
    }