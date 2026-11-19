from typing import List, Dict, Any, Optional
import requests
from modules.config import config


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
    ) -> List[Dict[str, Any]]:
        """Queries /requirements with up to 5 comma-separated keywords and optional skill filter."""
        if not config.requirements_api_key or not keywords:
            return []

        cleaned_keywords = [k.strip() for k in keywords if k.strip()][:5]
        if not cleaned_keywords:
            return []

        params: Dict[str, Any] = {
            "keyword": ",".join(cleaned_keywords),
            "limit": min(limit or config.requirements_limit, 500),
        }
        if skill:
            params["skill"] = skill

        try:
            resp = requests.get(
                f"{self.base_url}/requirements",
                headers=self.headers,
                params=params,
                timeout=20,
                verify=config.verify_ssl,
            )
            resp.raise_for_status()
            records = resp.json().get("data", [])

            # Fallback: if strict multi-keyword + skill filter returned nothing, broaden to primary keyword only
            if not records and (skill or len(cleaned_keywords) > 1):
                fallback_params = {
                    "keyword": cleaned_keywords[0],
                    "limit": params["limit"],
                }
                resp = requests.get(
                    f"{self.base_url}/requirements",
                    headers=self.headers,
                    params=fallback_params,
                    timeout=20,
                    verify=config.verify_ssl,
                )
                resp.raise_for_status()
                records = resp.json().get("data", [])

            return self._rank_and_extract_references(records)
        except Exception as e:
            print(f"[WARN] Requirements API request failed: {e}")
            return []

    @staticmethod
    def _rank_and_extract_references(records: List[Dict[str, Any]], max_refs: int = 5) -> List[Dict[str, Any]]:
        """
        Safely extracts optional text/document fields and prioritizes records
        that have actual JD text or led to a sent proposal.
        """
        enriched = []
        for rec in records:
            text_obj = rec.get("text") or {}
            skills_and_jd = (text_obj.get("skills_and_jd") or "").strip()
            description = (text_obj.get("description") or "").strip()
            client_message = (text_obj.get("client_message") or "").strip()

            docs = rec.get("client_documents") or []
            doc_snippets = [
                (d.get("text") or "").strip()[:500]
                for d in docs
                if isinstance(d, dict) and d.get("text")
            ]

            proposal_sent = bool((rec.get("proposal_sent") or {}).get("sent", False))

            # Score richness so we feed the best historical examples to the LLM
            richness = 0
            if skills_and_jd:
                richness += 3
            if description:
                richness += 2
            if doc_snippets:
                richness += 2
            if proposal_sent:
                richness += 1
            if client_message:
                richness += 1

            if richness == 0:
                continue

            enriched.append({
                "richness": richness,
                "skill": rec.get("skill"),
                "sub_skill": rec.get("sub_skill"),
                "skills_and_jd": skills_and_jd[:800],
                "description": description[:600],
                "client_message": client_message[:500],
                "doc_excerpt": doc_snippets[0] if doc_snippets else "",
                "matched_on": rec.get("matched_on") or [],
            })

        enriched.sort(key=lambda x: x["richness"], reverse=True)
        return enriched[:max_refs]