from modules.clients.requirements_api import RequirementsAPIClient
from modules.clients.bench_api import BenchAPIClient
from modules.ai_pipeline import AIPipeline
from modules.matcher import BenchMatcher
from modules.schemas import PipelineResult


class HiringPipeline:
    def __init__(self):
        self.req_client = RequirementsAPIClient()
        self.bench_client = BenchAPIClient()
        self.ai = AIPipeline()
        self.matcher = BenchMatcher()

    def run(self, user_input: str) -> PipelineResult:
        # 1. Fetch canonical VE skills (cached) and parse client query
        canonical_skills = self.req_client.get_skill_names()
        parsed_query = self.ai.parse_client_query(user_input, canonical_skills)

        if not parsed_query.is_valid_ve_request:
            return PipelineResult(parsed_query=parsed_query)

        # 2. Query VE Requirements API for historical reference JDs
        reference_jds = self.req_client.fetch_reference_requirements(
            keywords=parsed_query.search_keywords,
            skill=parsed_query.canonical_skill_filter,
        )

        # 3. Generate validated, humanized JD grounded in historical JDs
        generated_jd = self.ai.generate_humanized_jd(parsed_query, reference_jds)

        # 4. Fetch full bench pool (cached & deduplicated) and rank Top N matches
        bench_pool = self.bench_client.fetch_all_bench_resources()
        matches = self.matcher.match(parsed_query, generated_jd, bench_pool)

        return PipelineResult(
            parsed_query=parsed_query,
            reference_jds_used=len(reference_jds),
            generated_jd=generated_jd,
            matched_resources=matches,
        )