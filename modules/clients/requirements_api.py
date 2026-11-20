from typing import List, Dict, Any, Optional
import os
import requests
from modules.config import config
import re

class RequirementsAPIClient:
    def __init__(self):
        self.base_url = config.requirements_api_base
        self.headers = {"x-api-key": config.requirements_api_key}
        self._skills_cache: Optional[List[Dict[str, Any]]] = None

    def get_skills_taxonomy(self) -> List[Dict[str, Any]]:
        """Fetches and caches the canonical /skills list from VE."""
        if self._skills_cache is not None:
            return self._skills_cache

        if not config.requirements_api_key:
            return []

        try:
            resp = requests.get(
                f"{self.base_url}/skills",
                headers=self.headers,
                timeout=15,
            )
            if os.getenv("VE_DEBUG_API_RESPONSES", "false").strip().lower() == "true":
                print(f"[Requirements API /skills] HTTP {resp.status_code}\n{resp.text}", flush=True)
            resp.raise_for_status()
            self._skills_cache = resp.json().get("data", [])
            return self._skills_cache
        except Exception as e:
            print(f"[WARN] Could not fetch /skills taxonomy: {e}")
            return []

    def get_skill_names(self) -> List[str]:
        """Returns flat list of top-level canonical skill names."""
        taxonomy = self.get_skills_taxonomy()
        return [item.get("skill", "") for item in taxonomy if item.get("skill")]

    def fetch_reference_requirements(
        self,
        keywords: List[str],
        skill: Optional[str] = None,
        limit: Optional[int] = None,
        *,
        query: str = "",
        exact_keyword: Optional[str] = None,
        include_raw: bool = False,
        raise_on_error: bool = False,
    ) -> List[Dict[str, Any]]:
        keywords = [word.strip() for word in keywords if word.strip()][:5]
        if not config.requirements_api_key:
            if raise_on_error:
                raise RuntimeError("VE_REQUIREMENTS_API_KEY is not configured")
            return []
        if not keywords and not exact_keyword:
            return []

        params = {
            "keyword": exact_keyword or ",".join(keywords),
            "limit": min(500, max(1, limit or config.requirements_limit)),
        }
        if skill and not exact_keyword:
            params["skill"] = skill

        def fetch(request_params):
            response = requests.get(
                f"{self.base_url}/requirements",
                headers=self.headers,
                params=request_params,
                timeout=20,
                verify=config.verify_ssl,
                allow_redirects=False,
            )
            if os.getenv("VE_DEBUG_API_RESPONSES", "false").strip().lower() == "true":
                print(
                    f"[Requirements API /requirements] HTTP {response.status_code}\n{response.text}",
                    flush=True,
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Requirements API returned HTTP {response.status_code}"
                )
            records = response.json().get("data")
            if not isinstance(records, list):
                raise ValueError("Requirements API did not return a data array")
            return records

        try:
            records = fetch(params)
            if not records and keywords:
                fallback = {"keyword": keywords[0], "limit": params["limit"]}
                if fallback != params:
                    records = fetch(fallback)
            return self._rank_and_extract_references(
                records, query=query, include_raw=include_raw
            )
        except Exception as exc:
            if raise_on_error:
                raise
            print(f"[WARN] Requirements API request failed: {type(exc).__name__}")
            return []

    @staticmethod
    def _rank_and_extract_references(
        records: List[Dict[str, Any]],
        max_refs: int = 5,
        *,
        query: str = "",
        include_raw: bool = False,
    ) -> List[Dict[str, Any]]:
        def tokens(value):
            return set(re.findall(r"[\w+#]+", value.casefold()))

        ignore = {"a", "an", "the", "hire", "for", "with", "need", "and", "to"}
        query_terms = tokens(query) - ignore
        ranked = []

        for record in records:
            if not isinstance(record, dict):
                continue

            fields = record.get("text") or {}
            if not isinstance(fields, dict):
                fields = {}

            jd = str(fields.get("skills_and_jd") or "").strip()
            message = str(fields.get("client_message") or "").strip()
            docs = [
                doc["text"].strip()
                for doc in record.get("client_documents") or []
                if isinstance(doc, dict)
                and isinstance(doc.get("text"), str)
                and doc["text"].strip()
            ]
            document = docs[0] if docs else ""
            primary = jd or document or message
            if not primary:
                continue

            skill_text = f"{record.get('skill') or ''} {record.get('sub_skill') or ''}"
            fit = (
                2 * len(query_terms & tokens(skill_text))
                + 3 * len(query_terms & tokens(primary[:2200]))
                + len(query_terms & tokens(message[:1200]))
            )
            richness = 3 * bool(jd) + 2 * bool(document) + bool(message)
            reference = {
                "ref": record.get("ref"),
                "skill": record.get("skill"),
                "sub_skill": record.get("sub_skill"),
                "richness": richness,
                "skills_and_jd": jd[:2200],
                "client_message": message[:900] if message != jd else "",
                "doc_excerpt": document[:1500],
            }
            if include_raw:
                reference["source_record"] = record

            ranked.append(
                (fit, richness, " ".join(primary.casefold().split()), reference)
            )

        ranked.sort(key=lambda row: (-row[0], -row[1]))
        selected, seen_text, seen_refs = [], set(), set()
        for _, _, primary, reference in ranked:
            ref = reference["ref"]
            if primary in seen_text or (ref and ref in seen_refs):
                continue
            seen_text.add(primary)
            if ref:
                seen_refs.add(ref)
            selected.append(reference)
            if len(selected) >= max_refs:
                break

        return selected