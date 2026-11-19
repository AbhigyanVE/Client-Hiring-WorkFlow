import json
import re
from typing import List, Dict, Any
from openai import OpenAI
from modules.config import config
from modules.schemas import ParsedClientQuery, HumanizedJD, TimelineEstimate


class AIPipeline:
    def __init__(self):
        self.use_ai = config.use_openai_api and bool(config.openai_api_key)
        self.client = OpenAI(api_key=config.openai_api_key) if self.use_ai else None
        self.model = config.openai_model

    # ------------------------------------------------------------------
    # STEP 2: INTENT GUARDRAIL + PARAMETER & KEYWORD EXTRACTION
    # ------------------------------------------------------------------
    def parse_client_query(self, user_input: str, canonical_skills: List[str]) -> ParsedClientQuery:
        if self.use_ai and self.client:
            return self._openai_parse(user_input, canonical_skills)
        return self._fallback_parse(user_input)

    def _openai_parse(self, user_input: str, canonical_skills: List[str]) -> ParsedClientQuery:
        refusal_tone = (
            "Be witty and slightly sarcastic in `rejection_message` while clarifying VE only offers professional remote staffing."
            if config.quirky_mode
            else "Be polite, brief, and firm in `rejection_message`, clarifying VE only offers professional remote staffing."
        )

        skills_hint = ", ".join(canonical_skills[:60]) if canonical_skills else "Software Engineering, Digital Marketing, Virtual Assistants, IT, Accounts, Paralegal"

        system_prompt = f"""You are the Intent Classifier and Parameter Extractor for Virtual Employee Private Limited (VE).
VE provides remote professionals across: Software Engineering, AI/ML, IT Support, Digital Marketing, Virtual Assistants, Accounts/Finance, and Paralegal services.

RULES:
1. GUARDRAIL: If the user asks for anything illegal, explicit, offensive, or completely outside VE's remote staffing services, set `is_valid_ve_request=false` and provide `rejection_message`. {refusal_tone}
2. EXTRACTION: Extract ONLY what is present in the user's string. Leave unmentioned fields as null.
3. KEYWORDS: Generate 1 to 3 short, single-word or 2-word root `search_keywords` (e.g., ["python", "ai", "automation"]) with the MOST important core technology first. Avoid verbose phrases like "automation scripts".
4. CANONICAL SKILL: ONLY set `canonical_skill_filter` if the request matches an EXACT string from [{skills_hint}]. If unsure, set it to null.
5. INFERRED STACK: Populate `inferred_tech_stack` with 4-6 modern, industry-standard atomic tool/framework names (for AI automation include LLM/agent tools like LangChain, OpenAI, FastAPI alongside ML libraries).
6. BUDGET TIER: Map phrases like "cheap / low budget / startup budget" -> "low", "mid / moderate" -> "mid", "high / premium / top expert" -> "high". Leave null if unstated."""

        try:
            response = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ],
                response_format=ParsedClientQuery,
                temperature=0.1,
            )
            return response.choices[0].message.parsed
        except Exception as e:
            print(f"[WARN] OpenAI extraction failed ({e}). Switching to offline parser.")
            return self._fallback_parse(user_input)

    @staticmethod
    def _fallback_parse(user_input: str) -> ParsedClientQuery:
        text = user_input.lower()
        blocked = ["prostitute", "escort", "drugs", "hack bank", "illegal", "hitman"]
        if any(b in text for b in blocked):
            return ParsedClientQuery(
                is_valid_ve_request=False,
                rejection_message="Nice try, but Virtual Employee strictly provides remote tech, marketing, finance, legal, and admin talent.",
            )

        known_tech = [
            "python", "java", "javascript", "typescript", "react", "next.js", "angular",
            "node.js", "express.js", "php", "laravel", "wordpress", "flutter", "swift",
            "c#", ".net", "django", "fastapi", "flask", "mongodb", "postgresql", "mysql",
            "aws", "azure", "docker", "seo", "google ads", "quickbooks", "xero", "tally",
            "paralegal", "virtual assistant", "ai", "ml", "langchain", "rag",
        ]
        found_skills = [t for t in known_tech if re.search(rf"\b{re.escape(t)}\b", text)]

        budget_tier = None
        if any(w in text for w in ["low budget", "cheap", "affordable", "tight budget"]):
            budget_tier = "low"
        elif any(w in text for w in ["mid budget", "medium budget", "moderate"]):
            budget_tier = "mid"
        elif any(w in text for w in ["high budget", "premium", "enterprise", "expert"]):
            budget_tier = "high"

        keywords = found_skills[:5] if found_skills else [w for w in re.findall(r"[a-zA-Z0-9+#.-]+", text) if len(w) > 3][:5]

        return ParsedClientQuery(
            is_valid_ve_request=True,
            role_or_requirement=user_input.strip(),
            search_keywords=keywords,
            explicit_tech_stack=[s.title() for s in found_skills],
            inferred_tech_stack=[],
            budget_tier=budget_tier,
        )

    # ------------------------------------------------------------------
    # STEP 3: HUMANIZED JD GENERATION (GROUNDED IN HISTORICAL JDs)
    # ------------------------------------------------------------------
    def generate_humanized_jd(
        self,
        parsed_query: ParsedClientQuery,
        reference_reqs: List[Dict[str, Any]],
    ) -> HumanizedJD:
        if self.use_ai and self.client:
            return self._openai_generate_jd(parsed_query, reference_reqs)
        return self._fallback_generate_jd(parsed_query, reference_reqs)

    def _openai_generate_jd(
        self,
        parsed_query: ParsedClientQuery,
        reference_reqs: List[Dict[str, Any]],
    ) -> HumanizedJD:
        style_rule = (
            "SUMMARIZED STYLE: Keep `about_the_role` to 1-2 crisp sentences and `key_responsibilities` to 3-4 concise bullets."
            if config.response_style == "summarized"
            else "DETAILED STYLE: Write a comprehensive 3-4 sentence `about_the_role` and 5-6 detailed responsibility bullets."
        )

        system_prompt = f"""You are the JD Generation & Validation Engine for Virtual Employee (VE).
Synthesize the client's requirement and the retrieved historical VE client requirements into one clean, realistic, humanized Job Description.

RULES:
1. {style_rule}
2. GROUNDING & VALIDATION: Use the historical VE requirements to capture realistic responsibilities and domain skills, without copying unrelated client-specific company names or hallucinations.
3. SKILLS FORMAT (STRICT): Both `required_skills` and `inferred_skills` MUST be short 1-3 word skill/tool tags (e.g., "Python", "FastAPI", "LangChain", "RAG", "Docker"). NEVER write sentences and NEVER append "(inferred)" to tag names.
4. EXPERIENCE TIER: If the client specified a budget or seniority, align `experience_tier` (low -> Junior, mid -> Mid-Level, high -> Senior). Otherwise set `experience_tier` to "To be confirmed".
5. SILENT TIMELINE BUFFER: Calculate a realistic independent delivery timeline in weeks, internally setting `safety_buffer_weeks` to 1 or 2. In `total_realistic_timeline`, show only the combined total (e.g., "6-8 weeks") and NEVER mention the word "buffer" or "padding"."""

        user_payload = {
            "client_extracted_requirements": parsed_query.model_dump(),
            "historical_ve_reference_jds": reference_reqs,
        }

        try:
            response = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, indent=2)},
                ],
                response_format=HumanizedJD,
                temperature=0.3,
            )
            jd = response.choices[0].message.parsed
            jd.grounded_reference_count = len(reference_reqs)
            return jd
        except Exception as e:
            print(f"[WARN] OpenAI JD generation failed ({e}). Switching to offline JD builder.")
            return self._fallback_generate_jd(parsed_query, reference_reqs)

    @staticmethod
    def _fallback_generate_jd(
        parsed_query: ParsedClientQuery,
        reference_reqs: List[Dict[str, Any]],
    ) -> HumanizedJD:
        tier_map = {"low": "Junior", "mid": "Mid-Level", "high": "Senior"}
        exp_tier = tier_map.get(parsed_query.budget_tier or "", "To be confirmed")
        req_skills = parsed_query.explicit_tech_stack or parsed_query.search_keywords or ["General Technical Support"]

        return HumanizedJD(
            role_title=f"Dedicated Specialist — {parsed_query.role_or_requirement or 'Custom Requirement'}",
            service_category="Software Engineering",
            about_the_role=f"We are looking for a skilled remote professional to deliver on: {parsed_query.role_or_requirement}.",
            key_responsibilities=[
                "Design, build, and maintain core deliverables aligned with client requirements",
                "Collaborate directly with stakeholders and provide regular progress updates",
                "Ensure clean, reliable, and production-ready output",
            ],
            required_skills=req_skills,
            inferred_skills=parsed_query.inferred_tech_stack,
            experience_tier=exp_tier,
            timeline=TimelineEstimate(
                estimated_core_weeks=4,
                safety_buffer_weeks=2,
                total_realistic_timeline="5-6 weeks",
            ),
            grounded_reference_count=len(reference_reqs),
        )