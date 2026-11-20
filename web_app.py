"""Virtual Employee Hiring Workspace: generate a JD and rank bench profiles."""

import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import truststore

import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Response
from modules.schemas import HumanizedJD, TimelineEstimate

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


@lru_cache(maxsize=1)
def bench_client():
    return BenchAPIClient()


def fetch_bench():
    return bench_client().fetch_all_bench_resources(raise_on_error=True)

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


def rank_candidates(parsed, jd, rows, *, edited=False):
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
        if edited and len(draft_hits) != len(draft_skills):
            continue
        if not draft_hits and (draft_skills or not explicit):
            continue

        minimum_years = {"Mid-Level": 3, "Senior": 8}.get(jd.experience_tier)
        if minimum_years is not None and (years is None or years < minimum_years):
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
            **demo_values(str(row["profile_id"]), years),
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
    return [item[3] for item in ranked[:max(5, config.top_matches_limit)]]

def demo_values(profile_id, years):
    digest = hashlib.sha256(f"ve-demo:{profile_id}".encode()).digest()
    rate = None
    if years is not None and years >= 0:
        if years < 3:
            low, high = 7, 10
        elif years <= 10:
            low, high = 10, 16
        else:
            low, high = 13, 20
        rate = low + int.from_bytes(digest[:4], "big") % (high - low + 1)

    return {
        "demo_hourly_rate": rate,
        "demo_rating": (40 + digest[4] % 11) / 10,
    }

def public_jd(jd):
    payload = jd.model_dump(
        mode="json",
        exclude={
            "grounded_reference_count": True,
            "timeline": {"estimated_core_weeks", "safety_buffer_weeks"},
        },
    )
    payload["job_description"] = payload["about_the_role"]
    return payload


def load_bench():
     try:
         return fetch_bench()
     except Exception as exc:
        logger.warning("Bench lookup failed: %s: %s", type(exc).__name__, exc)
        return None

def match_payload(parsed, jd, bench, *, edited=False):
    if bench is None:
        return {"matches": [], "match_status": "unavailable"}

    matches = rank_candidates(parsed, jd, bench, edited=edited)
    result = {
        "matches": matches,
        "match_status": "ready" if matches else "no_matches",
    }
    if not matches:
        result["match_message"] = (
            "No available employees match this job description right now. "
            "Contact Virtual Employee and our team can help you find a match."
        )
    return result


def source_review_allowed(token):
    return bool(
        config.source_review_token
        and token
        and secrets.compare_digest(
            token.encode(), config.source_review_token.encode()
        )
    )


def build_job_options(requirement, response, review_token=None, *, url_keyword=False):
    requirement = requirement.strip()
    if not requirement:
        raise HTTPException(422, "Enter a role or project requirement.")

    review_sources = source_review_allowed(review_token)
    if review_token is not None and not review_sources:
        raise HTTPException(403, "Source review is not authorized.")

    try:
        client = requirements_client()
        ai = AIPipeline()
        parsed = ai.parse_client_query(requirement, client.get_skill_names())
    except Exception:
        logger.exception("Could not parse hiring requirements")
        raise HTTPException(502, "Couldn’t process your requirements.")

    if not parsed.is_valid_ve_request:
        raise HTTPException(
            422, parsed.rejection_message or "Describe a remote role or project."
        )

    try:
        references = client.fetch_reference_requirements(
            keywords=parsed.search_keywords or [requirement[:120]],
            skill=parsed.canonical_skill_filter,
            query=requirement,
            exact_keyword=requirement if url_keyword else None,
            include_raw=review_sources,
            raise_on_error=True,
        )
    except Exception as exc:
        logger.warning("Requirements lookup failed: %s", type(exc).__name__)
        raise HTTPException(502, "Couldn’t retrieve job requirements.") from exc

    def draft(reference):
        safe_reference = {
            key: value for key, value in reference.items() if key != "source_record"
        }
        return ai.generate_humanized_jd(parsed, [safe_reference]), reference

    with ThreadPoolExecutor(max_workers=5) as executor:
        drafts = list(executor.map(draft, references))

    bench = load_bench()
    options, seen = [], set()
    for jd, reference in drafts:
        if not jd.grounded_reference_count:
            continue
        signature = (
            " ".join(jd.about_the_role.casefold().split()),
            tuple(sorted(skill.strip().casefold() for skill in jd.required_skills)),
        )
        if signature in seen:
            continue
        seen.add(signature)

        option = {
            "id": f"jd-{len(options) + 1}",
            "jd": public_jd(jd),
            **match_payload(parsed, jd, bench),
        }
        if review_sources:
            option["source_record"] = reference["source_record"]
        options.append(option)

    if not options:
        jd = drafts[0][0] if drafts else ai.generate_humanized_jd(parsed, [])
        options.append({
            "id": "jd-1",
            "jd": public_jd(jd),
            **match_payload(parsed, jd, bench),
        })

    first = options[0]
    response.headers["Cache-Control"] = "no-store"
    result = {
        "jd": first["jd"],
        "matches": first["matches"],
        "match_status": first["match_status"],
        "jd_options": options,
    }
    if "match_message" in first:
        result["match_message"] = first["match_message"]
    return result


@app.post("/api/jd")
def generate_jd(
    body: JDRequest,
    response: Response,
    x_source_review_token: str | None = Header(default=None),
):
    return build_job_options(body.requirement, response, x_source_review_token)


@app.get("/api/jd")
def generate_jd_from_keyword(
    response: Response,
    keyword: str = Query(min_length=1, max_length=4000),
    x_source_review_token: str | None = Header(default=None),
):
    return build_job_options(
        keyword, response, x_source_review_token, url_keyword=True
    )


class EditedJDRequest(BaseModel):
    role_title: str = Field(min_length=1, max_length=200)
    job_description: str = Field(min_length=1, max_length=4000)
    service_category: str = Field(default="", max_length=120)
    key_responsibilities: list[str] = Field(default_factory=list, max_length=20)
    required_skills: list[str] = Field(default_factory=list, max_length=30)
    inferred_skills: list[str] = Field(default_factory=list, max_length=30)
    experience_tier: Literal[
        "Junior", "Mid-Level", "Senior", "To be confirmed"
    ] = "To be confirmed"


@app.post("/api/jd/rematch")
def rematch_edited_jd(body: EditedJDRequest, response: Response):
    if not body.role_title.strip() or not body.job_description.strip():
        raise HTTPException(422, "Provide a job title and description.")
    if any(len(skill) > 100 for skill in body.required_skills + body.inferred_skills):
        raise HTTPException(422, "Each skill must be at most 100 characters.")
    if any(len(item) > 500 for item in body.key_responsibilities):
        raise HTTPException(422, "Each responsibility must be at most 500 characters.")

    edited_scope = "\n".join([
        body.role_title, body.job_description, *body.key_responsibilities,
    ])
    parsed = AIPipeline().parse_client_query(edited_scope, [])
    if not parsed.is_valid_ve_request:
        raise HTTPException(
            422, parsed.rejection_message or "Describe a remote role or project."
        )

    jd = HumanizedJD(
        role_title=body.role_title,
        about_the_role=body.job_description,
        service_category=body.service_category,
        key_responsibilities=body.key_responsibilities,
        required_skills=unique_skills(body.required_skills) or parsed.explicit_tech_stack,
        inferred_skills=unique_skills(body.inferred_skills + parsed.explicit_tech_stack),
        experience_tier=body.experience_tier,
        timeline=TimelineEstimate(
            estimated_core_weeks=0,
            safety_buffer_weeks=1,
            total_realistic_timeline="To be confirmed",
        ),
    )
    parsed = parsed.model_copy(
        update={"explicit_tech_stack": [], "budget_tier": None}
    )
    response.headers["Cache-Control"] = "no-store"
    return match_payload(parsed, jd, load_bench(), edited=True)