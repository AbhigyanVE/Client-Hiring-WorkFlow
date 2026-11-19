import json
from modules.schemas import PipelineResult


def print_pipeline_result(result: PipelineResult) -> None:
    print("\n\033[96m--- 1. Extracted Parameters ---\033[0m")
    print(result.parsed_query.model_dump_json(indent=2))

    if not result.parsed_query.is_valid_ve_request:
        print(f"\n\033[91m[Rejected]: {result.parsed_query.rejection_message}\033[0m\n")
        return

    print(f"\n\033[90m[Grounded using {result.reference_jds_used} historical VE requirement(s)]\033[0m")

    if result.generated_jd:
        print("\n\033[95m--- 2. Generated Humanized JD (JSON) ---\033[0m")
        print(result.generated_jd.model_dump_json(indent=2))

    print("\n\033[92m--- 3. Top Matched Bench Resources (Best to Worst) ---\033[0m")
    if not result.matched_resources:
        print("NA")
    else:
        for cand in result.matched_resources:
            skills_str = ", ".join(cand.matched_skills) if cand.matched_skills else "Category/Role Fit"
            print(
                f"{cand.rank}. {cand.name} ({cand.designation} | {cand.employee_id}) "
                f"- Exp: {cand.total_experience_years} yrs | Bench: {cand.bench_hours} hrs"
            )
            print(f"   Score: {cand.score} | Category: {cand.skill_category} | Matched: {skills_str}")
    print()


def save_latest_result(result: PipelineResult, filepath: str = "latest_jd_payload.json") -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2)