import re
from typing import List, Dict, Any
from modules.config import config
from modules.schemas import ParsedClientQuery, HumanizedJD, MatchedCandidate


class BenchMatcher:
    def __init__(self):
        self.low_max = config.budget_low_max_yoe
        self.mid_max = config.budget_mid_max_yoe
        self.top_limit = config.top_matches_limit

    @staticmethod
    def _skill_present(skill: str, haystack: str) -> bool:
        """Uses boundary-aware regex so 'java' does not falsely match 'javascript'."""
        escaped = re.escape(skill.lower().strip())
        if not escaped:
            return False
        pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
        return bool(re.search(pattern, haystack))

    def _passes_budget_tier(self, budget_tier: str | None, exp_years: float) -> bool:
        if not budget_tier:
            return True
        if budget_tier == "low":
            return exp_years <= self.low_max
        if budget_tier == "mid":
            return self.low_max < exp_years <= self.mid_max
        if budget_tier == "high":
            return exp_years > self.mid_max
        return True

    def match(
        self,
        parsed_query: ParsedClientQuery,
        generated_jd: HumanizedJD,
        bench_pool: List[Dict[str, Any]],
    ) -> List[MatchedCandidate]:
        primary_skills = list(
            dict.fromkeys(
                [s.strip() for s in (parsed_query.explicit_tech_stack + generated_jd.required_skills) if s.strip()]
            )
        )
        secondary_skills = list(
            dict.fromkeys(
                [s.strip() for s in (parsed_query.inferred_tech_stack + generated_jd.inferred_skills) if s.strip()]
            )
        )
        target_category = (generated_jd.service_category or "").lower()
        role_text = (parsed_query.role_or_requirement or generated_jd.role_title or "").lower()

        scored: List[Dict[str, Any]] = []

        for emp in bench_pool:
            # 1. Availability gate
            if (emp.get("bench_hours") or 0) <= 0:
                continue

            try:
                exp_years = float(emp.get("total_experience_years") or 0)
            except (ValueError, TypeError):
                exp_years = 0.0

            # 2. Strict budget-to-YoE gate
            if not self._passes_budget_tier(parsed_query.budget_tier, exp_years):
                continue

            skills_search = str(emp.get("skills_search") or "").lower()
            job_profile = str(emp.get("job_profile") or "").lower()
            designation = str(emp.get("designation") or "").lower()
            skill_category = str(emp.get("skill_category_name") or "").lower()
            combined_text = f"{skills_search} {job_profile} {designation}"

            score = 0.0
            matched_skills: List[str] = []

            # 3. Primary skill matching (heavy weight)
            primary_hits = 0
            for skill in primary_skills:
                if self._skill_present(skill, combined_text):
                    score += 15.0
                    primary_hits += 1
                    matched_skills.append(skill)

            # 4. Secondary / inferred skill matching (medium weight)
            for skill in secondary_skills:
                if skill not in matched_skills and self._skill_present(skill, combined_text):
                    score += 6.0
                    matched_skills.append(skill)

            # 5. Category & designation alignment
            if target_category and target_category in skill_category:
                score += 5.0
            if role_text and any(tok in designation for tok in role_text.split() if len(tok) > 3):
                score += 4.0

            # Hard relevance gate: if specific skills were required and 0 matched, drop candidate
            if primary_skills and primary_hits == 0 and len(matched_skills) == 0:
                continue

            if score < 6.0:
                continue

            # Experience tie-breaker within the allowed budget bracket
            score += min(exp_years, 15.0) * 0.15

            scored.append({
                "score": round(score, 2),
                "emp": emp,
                "exp_years": exp_years,
                "matched_skills": matched_skills,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)

        results: List[MatchedCandidate] = []
        for idx, item in enumerate(scored[: self.top_limit], start=1):
            emp = item["emp"]
            results.append(
                MatchedCandidate(
                    rank=idx,
                    score=item["score"],
                    profile_id=int(emp.get("profile_id") or 0),
                    employee_id=str(emp.get("employee_id") or ""),
                    name=str(emp.get("name") or "Unknown"),
                    designation=str(emp.get("designation") or ""),
                    department=str(emp.get("department") or ""),
                    skill_category=str(emp.get("skill_category_name") or ""),
                    total_experience_years=item["exp_years"],
                    bench_hours=int(emp.get("bench_hours") or 0),
                    matched_skills=item["matched_skills"],
                    email=str(emp.get("email") or ""),
                )
            )
        return results