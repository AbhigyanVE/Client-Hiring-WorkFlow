import time
import os
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
        self._loaded = False

    def fetch_all_bench_resources(
         self, force_refresh: bool = False, *, raise_on_error: bool = False
     ) -> List[Dict[str, Any]]:
        if (
            not force_refresh
            and self._loaded
            and time.time() - self._last_fetched < self.cache_ttl
        ):
            return self._cache

        try:
            if not config.bench_api_key:
                raise RuntimeError("VE_BENCH_API_KEY is not configured")

            records, seen = [], set()
            page_limit, offset = 200, 0
            for _ in range(200):
                # page_limit = 200 if total is None else min(200, total - offset)
                response = requests.get(
                    self.url,
                    headers=self.headers,
                    params={"limit": page_limit, "offset": offset},
                    timeout=20,
                    verify=config.verify_ssl,
                    allow_redirects=False,
                )
                if os.getenv("VE_DEBUG_API_RESPONSES", "false").strip().lower() == "true":
                    print(
                        f"[Bench API] HTTP {response.status_code} offset={offset}\n{response.text}",
                        flush=True,
                    )
                if response.status_code != 200:
                    raise RuntimeError(f"Bench API returned HTTP {response.status_code}")

                payload = response.json()
                batch = payload.get("data")
                if payload.get("success") is False or not isinstance(batch, list):
                    raise RuntimeError("Unexpected bench response")

                new_in_batch = 0                
                for row in batch:
                    if not isinstance(row, dict):
                        raise RuntimeError(f"Invalid bench profile at offset={offset}")
                    profile_id = str(row.get("profile_id") or row.get("employee_id") or "").strip()
                    if not profile_id or profile_id in seen:
                        continue
                    seen.add(profile_id)
                    records.append({**row, "profile_id": profile_id})
                    new_in_batch += 1

                if len(batch) < page_limit or new_in_batch == 0:
                    break
                offset += page_limit
            else:
                raise RuntimeError("Bench pagination exceeded the page limit")

            self._cache = records
            self._last_fetched = time.time()
            self._loaded = True
            return records
        except Exception as exc:
            if raise_on_error:
                raise
            print(f"[WARN] Bench API request failed: {type(exc).__name__}")
            return self._cache