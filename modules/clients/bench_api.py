import time
from typing import List, Dict, Any
import requests
from modules.config import config


class BenchAPIClient:
    def __init__(self, cache_ttl_seconds: int = 300):
        self.url = config.bench_api_url
        self.headers = {"x-api-key": config.bench_api_key}
        self.cache_ttl = cache_ttl_seconds
        self._cache: List[Dict[str, Any]] = []
        self._last_fetched: float = 0.0

    def fetch_all_bench_resources(
        self, force_refresh: bool = False, *, raise_on_error: bool = False
    ) -> List[Dict[str, Any]]:
        """Fetches, paginates, and deduplicates all available bench employees."""
        now = time.time()
        if not force_refresh and self._cache and (now - self._last_fetched) < self.cache_ttl:
            return self._cache

        if not config.bench_api_key:
            if raise_on_error:
                raise RuntimeError("VE_BENCH_API_KEY is missing")
            print("[ERROR] VE_BENCH_API_KEY is missing in .env")
            return []

        seen_ids = set()
        all_records: List[Dict[str, Any]] = []
        limit = 200
        offset = 0

        try:
            while True:
                resp = requests.get(
                    self.url,
                    headers=self.headers,
                    params={"limit": limit, "offset": offset},
                    timeout=20,
                )
                resp.raise_for_status()
                payload = resp.json()
                batch = payload.get("data")
                if payload.get("success") is False or not isinstance(batch, list):
                    raise RuntimeError("Unexpected bench response: expected a data array")

                if not batch:
                    break

                new_in_batch = 0
                for emp in batch:
                    emp_id = emp.get("employee_id") or emp.get("profile_id")
                    if emp_id and emp_id not in seen_ids:
                        seen_ids.add(emp_id)
                        all_records.append(emp)
                        new_in_batch += 1

                # Stop if API returned fewer than requested or no new unique records
                if len(batch) < limit or new_in_batch == 0:
                    break

                offset += limit

            self._cache = all_records
            self._last_fetched = now
            return self._cache
        except Exception as e:
            if raise_on_error:
                raise
            print(f"[ERROR] Bench API request failed: {e}")
            return self._cache  # Return stale cache if available