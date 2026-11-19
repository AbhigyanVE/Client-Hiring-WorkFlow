from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ParsedClientQuery(BaseModel):
    """Step 2 Output: Guardrail check + extracted search parameters."""
    is_valid_ve_request: bool = Field(
        description=(
            "True if the request fits Virtual Employee's remote staffing services "
            "(Software Engineering, AI/ML, IT, Digital Marketing, Virtual Assistants, Accounts, Paralegal). "
            "False if inappropriate, illegal, offensive, or completely unrelated."
        )
    )
    rejection_message: Optional[str] = Field(
        None,
        description="Witty or firm refusal if is_valid_ve_request is False. Null otherwise.",
    )
    role_or_requirement: Optional[str] = Field(
        None,
        description="Core role or project goal (e.g., 'Python Developer', 'Payment Software').",
    )
    search_keywords: List[str] = Field(
        default_factory=list,
        description="Up to 5 concise keywords to query the VE historical requirements API.",
    )
    canonical_skill_filter: Optional[str] = Field(
        None,
        description="Exact skill name from VE's /skills taxonomy if a clear match exists, else null.",
    )
    explicit_tech_stack: List[str] = Field(
        default_factory=list,
        description="Specific technologies, tools, or platforms explicitly named by the client.",
    )
    inferred_tech_stack: List[str] = Field(
        default_factory=list,
        description="Recommended skills/tools inferred from the use case if the client didn't specify a stack.",
    )
    budget_tier: Optional[Literal["low", "mid", "high"]] = Field(
        None,
        description="Normalized budget tier ('low', 'mid', 'high') if stated or clearly implied, else null.",
    )
    budget_amount: Optional[float] = Field(
        None,
        description="Numeric budget figure if mentioned, else null.",
    )
    budget_period: Optional[Literal["hourly", "monthly", "annual", "fixed"]] = Field(
        None,
        description="Billing cycle attached to budget_amount, else null.",
    )
    experience_level: Optional[Literal["junior", "mid", "senior"]] = Field(
        None,
        description="Requested seniority level if explicitly mentioned, else null.",
    )
    headcount: Optional[int] = Field(
        None,
        description="Number of resources requested, if specified.",
    )


class TimelineEstimate(BaseModel):
    estimated_core_weeks: int = Field(description="Realistic base build time in weeks.")
    safety_buffer_weeks: Literal[1, 2] = Field(
        description="Internal 1-2 week buffer for QA/deployment. NEVER mention 'buffer' in text."
    )
    total_realistic_timeline: str = Field(
        description="Combined delivery estimate (e.g., '6-8 weeks'). NEVER mention the word 'buffer'."
    )


class HumanizedJD(BaseModel):
    """Step 3 Output: Validated, humanized JD grounded in VE's past requirements."""
    role_title: str
    service_category: str = Field(
        description="e.g., Software Engineering, Digital Marketing, Virtual Assistants, IT, Accounts, Paralegal."
    )
    about_the_role: str = Field(description="Clear, friendly, realistic role summary.")
    key_responsibilities: List[str] = Field(description="Concrete, actionable responsibilities.")
    required_skills: List[str] = Field(
        description="4 to 8 atomic 1-3 word skill/tool tags ONLY (e.g., 'Python', 'FastAPI', 'REST API', 'OpenAI'). NEVER write full sentences."
    )
    inferred_skills: List[str] = Field(
        default_factory=list,
        description="4 to 6 atomic 1-3 word AI-recommended skill tags ONLY (e.g., 'LangChain', 'Docker', 'PostgreSQL'). NEVER write sentences or append '(inferred)'."
    )
    experience_tier: Literal["Junior", "Mid-Level", "Senior", "To be confirmed"]
    timeline: TimelineEstimate
    grounded_reference_count: int = Field(
        default=0,
        description="Number of historical VE requirements used to ground this JD.",
    )


class MatchedCandidate(BaseModel):
    """Step 4 Output: Ranked bench resource."""
    rank: int
    score: float
    profile_id: int
    employee_id: str
    name: str
    designation: str
    department: str
    skill_category: str
    total_experience_years: float
    bench_hours: int
    matched_skills: List[str]
    email: str


class PipelineResult(BaseModel):
    """Unified output payload returned to CLI or web_app.py."""
    parsed_query: ParsedClientQuery
    reference_jds_used: int = 0
    generated_jd: Optional[HumanizedJD] = None
    matched_resources: List[MatchedCandidate] = Field(default_factory=list)