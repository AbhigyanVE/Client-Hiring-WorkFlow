import truststore
truststore.inject_into_ssl()

from modules.config import config
from modules.pipeline import HiringPipeline
from modules.utils import print_pipeline_result, save_latest_result


def main():
    print("=" * 72)
    print("VIRTUAL EMPLOYEE (VE) — AI ZERO-QUESTION HIRING PIPELINE (CLI)")
    print(
        f"LLM: {'OpenAI (' + config.openai_model + ')' if config.use_openai_api else 'Offline Fallback'} | "
        f"Style: {config.response_style} | "
        f"Budget YoE Tiers: Low<= {config.budget_low_max_yoe}, Mid<= {config.budget_mid_max_yoe}, High> {config.budget_mid_max_yoe}"
    )
    print("Type 'exit' or 'quit' to stop.")
    print("=" * 72)

    pipeline = HiringPipeline()

    while True:
        try:
            user_input = input("\n\033[93mClient Requirement:\033[0m ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                print("Exiting...")
                break

            print("\033[90m[Running pipeline: Parse -> Reference JDs -> Generate JD -> Match Bench...]\033[0m")
            result = pipeline.run(user_input)
            print_pipeline_result(result)
            save_latest_result(result)

        except KeyboardInterrupt:
            print("\nSession terminated.")
            break
        except Exception as e:
            print(f"\n\033[91m[Runtime Error]: {e}\033[0m")


if __name__ == "__main__":
    main()