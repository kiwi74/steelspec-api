"""
Section matcher — loads the steel_sections table FROM SUPABASE
(not a local JSON file) so the API service always matches against
the same reference data the rest of the app uses.
"""
import re
from app.supabase_client import supabase


class SectionMatcher:
    def __init__(self):
        # Pull the full reference table once at startup
        result = supabase.table("steel_sections").select("*").execute()
        self._lookup = {self._normalise(row["name"]): row for row in result.data}

    def _normalise(self, name: str) -> str:
        return re.sub(r"\s+", "", name.strip().upper())

    def match(self, raw_name: str):
        candidates = [self._normalise(raw_name)]
        # Handle missing trailing .0
        base = self._normalise(raw_name)
        if "." not in base:
            candidates += [base + f".{d}" for d in range(10)]
        for c in candidates:
            if c in self._lookup:
                return self._lookup[c]
        return None

    def get_section_regex(self) -> re.Pattern:
        patterns = [
            r"\d{3}UB\d+\.?\d*", r"\d{3}UC\d+\.?\d*", r"\d{2,3}PFC",
            r"\d+x\d+x\d+\.?\d*(?:RHS|SHS|EA|UA)", r"\d+\.?\d*x\d+\.?\d*CHS", r"\d+x\d+FL",
        ]
        return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
