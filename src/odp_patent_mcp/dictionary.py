"""ODP Field Dictionary with synonym and description-based lookup."""

from typing import Dict, List, Set, Tuple
import re

# Structured field dictionary built from data_dictionary.txt
FIELD_DICTIONARY = {
    "applicationMetaData.patentNumber": {
        "type": "String",
        "description": "Unique number assigned by the USPTO to a granted/issued patent.",
        "synonyms": ["patent ID", "grant number", "issued number", "patent number"],
    },
    "applicationMetaData.firstInventorName": {
        "type": "String",
        "description": "Name of the inventor with Rank One. Listed as first inventor in the patent application.",
        "synonyms": ["primary inventor", "lead inventor", "first inventor", "top inventor name"],
    },
    "applicationMetaData.firstApplicantName": {
        "type": "String",
        "description": "Name of the Applicant with Rank One. Listed as first applicant in the patent application.",
        "synonyms": ["primary applicant", "lead applicant", "first filer", "applicant name", "company"],
    },
    "applicationMetaData.filingDate": {
        "type": "Date",
        "description": "Date on which a patent application was filed and received in the USPTO.",
        "synonyms": ["file date", "submission date", "received date", "application date"],
    },
    "applicationMetaData.inventionTitle": {
        "type": "String",
        "description": "Title of the invention/application.",
        "synonyms": ["invention name", "patent title", "app title"],
    },
    "applicationMetaData.applicationStatusCode": {
        "type": "String",
        "description": "Classifies the application by its status relative to the total application process.",
        "synonyms": ["status code", "app status", "process stage", "application stage"],
    },
    "applicationMetaData.cpcClassificationBag": {
        "type": "String",
        "description": "All the CPCs associated to application.",
        "synonyms": ["CPC codes", "classification codes", "patent categories", "cooperative patent classes"],
    },
    "applicationMetaData.grantDate": {
        "type": "Date",
        "description": "The date a patent was granted.",
        "synonyms": ["issue date", "granted date", "award date"],
    },
    "applicationMetaData.examinerNameText": {
        "type": "String",
        "description": "Name of the patent examiner with signatory authority.",
        "synonyms": ["examiner name", "reviewer name", "patent officer"],
    },
    "applicationMetaData.applicationStatusDescriptionText": {
        "type": "String",
        "description": "Status of the application (e.g. new = new application).",
        "synonyms": ["status text", "app status description", "stage description"],
    },
    "applicationMetaData.effectiveFilingDate": {
        "type": "Date",
        "description": "The date the patent case qualified as having been 'filed' (can be later than the filing date).",
        "synonyms": ["effective date", "qualified filing date", "official filing date"],
    },
    "patentTermAdjustmentData.adjustmentTotalQuantity": {
        "type": "Number",
        "description": "Summation of non-overlapping USPTO delays minus applicant delays.",
        "synonyms": ["total adjustment", "net delay", "final PTA", "patent term adjustment"],
    },
    "applicationMetaData.applicantBag.applicantNameText": {
        "type": "String",
        "description": "Applicant's name text.",
        "synonyms": ["applicant name", "filer name", "submitter name"],
    },
    "parentContinuityBag.parentApplicationNumberText": {
        "type": "String",
        "description": "Application number of the parent application.",
        "synonyms": ["parent app number", "prior app ID", "parent filing number"],
    },
    "childContinuityBag.childApplicationNumberText": {
        "type": "String",
        "description": "Child application number, unique value assigned by the USPTO.",
        "synonyms": ["child app number", "subsequent app ID", "child filing number"],
    },
    "assignmentBag.assigneeNameText": {
        "type": "String",
        "description": "Person or entity that has the property rights to the patent.",
        "synonyms": ["assignee", "new owner", "buyer name", "patent holder"],
    },
    "recordAttorney.powerOfAttorneyBag.firstName": {
        "type": "String",
        "description": "Attorney's first name.",
        "synonyms": ["attorney given name", "lawyer first", "attorney first name"],
    },
    "recordAttorney.powerOfAttorneyBag.lastName": {
        "type": "String",
        "description": "Attorney's last name.",
        "synonyms": ["attorney surname", "lawyer last", "attorney last name"],
    },
}


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, remove extra spaces."""
    return re.sub(r'\s+', ' ', text.lower().strip())


def _extract_keywords(text: str) -> Set[str]:
    """Extract significant keywords from text."""
    normalized = _normalize_text(text)
    # Split on spaces and filter short words
    words = [w for w in normalized.split() if len(w) > 2]
    return set(words)


def lookup_fields(user_query: str) -> Dict[str, Dict]:
    """
    3-tier lookup: synonym → description → field name.

    Returns dict of matched fields with their full metadata.
    """
    query_norm = _normalize_text(user_query)
    query_keywords = _extract_keywords(user_query)
    matched = {}

    for field_path, field_info in FIELD_DICTIONARY.items():
        # Tier 1: Exact synonym match
        for syn in field_info.get("synonyms", []):
            if _normalize_text(syn) == query_norm or _normalize_text(syn) in query_norm:
                matched[field_path] = field_info
                break

        if field_path in matched:
            continue

        # Tier 2: Description semantic match (keyword overlap)
        description = field_info.get("description", "")
        desc_keywords = _extract_keywords(description)
        keyword_overlap = query_keywords & desc_keywords

        # If >30% of query keywords match description, consider it a match
        if len(query_keywords) > 0 and len(keyword_overlap) / len(query_keywords) >= 0.3:
            matched[field_path] = field_info
            continue

        # Tier 3: Fuzzy field name match
        field_name_part = field_path.split('.')[-1].lower()
        if field_name_part in query_norm or query_norm in field_name_part:
            matched[field_path] = field_info

    return matched


def expand_query_to_lucene_clauses(user_input: str) -> List[str]:
    """
    Expand a user query into Lucene clauses using field matching and synonyms.

    Example:
        "unique number assigned by USPTO"
        → matches patentNumber via description
        → returns ["applicationMetaData.patentNumber:..."]
    """
    matched_fields = lookup_fields(user_input)

    clauses = []
    for field_path in matched_fields:
        # For now, add the field to the clause
        # The calling code will decide how to quote/format the value
        clauses.append(field_path)

    return clauses


def get_field_info(field_path: str) -> Dict:
    """Get full info for a specific field."""
    return FIELD_DICTIONARY.get(field_path, {})


def get_all_fields() -> Dict[str, Dict]:
    """Return the complete dictionary."""
    return FIELD_DICTIONARY


def get_searchable_fields_reference() -> str:
    """Generate a human-readable reference of searchable fields and synonyms."""
    lines = ["SEARCHABLE FIELDS & SYNONYMS\n"]

    for field_path, info in sorted(FIELD_DICTIONARY.items()):
        lines.append(f"\n{field_path}")
        lines.append(f"  Type: {info.get('type', 'Unknown')}")
        lines.append(f"  Description: {info.get('description', 'N/A')}")
        synonyms = info.get('synonyms', [])
        if synonyms:
            lines.append(f"  Synonyms: {', '.join(synonyms)}")

    return "\n".join(lines)
