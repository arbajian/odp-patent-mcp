"""
USPTO Patent MCP Server - ODP Only

This file provides a Model Context Protocol (MCP) server that exposes tools for
interacting with the USPTO Open Data Portal (ODP) API only:

- api.uspto.gov - Metadata, continuity information, transactions, assignments,
  attorney information, documents, and patent applications search

The server uses stdio transport for Claude Code/Cursor integration.

Version: 0.9.5
"""
import atexit
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from odp_patent_mcp.config import config
from odp_patent_mcp.constants import (
    Sources, Fields, Defaults, PatentsViewEndpoints
)
from odp_patent_mcp.util.errors import ApiError, is_error
from odp_patent_mcp.util.validation import validate_patent_number, validate_app_number
from odp_patent_mcp.util.response import (
    ResponseEnvelope, check_and_truncate, estimate_tokens
)
from odp_patent_mcp.resources import (
    get_cpc_section_info, get_cpc_subsection_info,
    get_status_code_info, get_all_status_codes,
    get_data_source_info, get_all_data_sources,
    get_search_syntax_guide, CPC_SECTIONS, DATA_SOURCES
)
from odp_patent_mcp.prompts import get_prompt, list_prompts, PROMPTS
from odp_patent_mcp.uspto.api_uspto_gov import ApiUsptoClient

# Initialize FastMCP server
mcp = FastMCP("odp_patent_tools")

# Set up logging with configured level
logging.basicConfig(
    level=config.get_log_level(),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger('odp_patent_mcp')

# Validate configuration
config.validate()

# Create client instance for USPTO ODP API
api_client = ApiUsptoClient()


# Register cleanup handler
async def cleanup():
    """Clean up resources on shutdown."""
    logger.info("Shutting down ODP Patent MCP server, cleaning up resources...")
    try:
        await api_client.close()
        logger.info("Cleanup completed successfully")
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")


# Register cleanup with atexit (best effort for stdio shutdown)
def sync_cleanup():
    """Synchronous cleanup wrapper for atexit."""
    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(cleanup())
                return
        except RuntimeError:
            pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(cleanup())
        finally:
            loop.close()
    except Exception as e:
        logger.debug(f"Cleanup during shutdown (non-critical): {str(e)}")


atexit.register(sync_cleanup)


# =====================================================================
# MCP Resources - Static data accessible via @ mentions
# =====================================================================

@mcp.resource("patents://cpc/{code}")
async def resource_cpc_classification(code: str) -> str:
    """Get CPC classification code information.

    Returns details about a CPC (Cooperative Patent Classification) code
    including section, class, and subclass information.
    """
    if len(code) == 1:
        info = get_cpc_section_info(code)
    else:
        info = get_cpc_subsection_info(code)
    return json.dumps(info, indent=2)


@mcp.resource("patents://cpc")
async def resource_cpc_sections() -> str:
    """Get all CPC section overview.

    Returns summary of all 9 CPC sections (A-H, Y) with their titles
    and descriptions for patent classification reference.
    """
    sections = {
        code: {"title": data["title"], "description": data["description"]}
        for code, data in CPC_SECTIONS.items()
    }
    return json.dumps(sections, indent=2)


@mcp.resource("patents://status-codes")
async def resource_status_codes() -> str:
    """Get USPTO application status code definitions.

    Returns all status codes used in patent application tracking
    with descriptions and examination stages.
    """
    return json.dumps(get_all_status_codes(), indent=2)


@mcp.resource("patents://status-codes/{code}")
async def resource_status_code(code: str) -> str:
    """Get a specific USPTO status code definition."""
    return json.dumps(get_status_code_info(code), indent=2)


@mcp.resource("patents://sources")
async def resource_data_sources() -> str:
    """Get information about available patent data sources.

    Returns details about all integrated APIs including coverage,
    rate limits, authentication requirements, and best use cases.
    """
    return json.dumps(get_all_data_sources(), indent=2)


@mcp.resource("patents://sources/{source}")
async def resource_data_source(source: str) -> str:
    """Get information about a specific data source."""
    return json.dumps(get_data_source_info(source), indent=2)


@mcp.resource("patents://search-syntax")
async def resource_search_syntax() -> str:
    """Get search query syntax guide for all APIs.

    Returns documentation on query syntax for PPUBS, PatentsView,
    and ODP APIs with examples.
    """
    return get_search_syntax_guide()


# =====================================================================
# MCP Prompts - Workflow templates accessible via / commands
# =====================================================================

@mcp.prompt()
async def prior_art_search() -> str:
    """Guide for conducting a comprehensive prior art search.

    USE THIS PROMPT WHEN: You need to find existing patents and publications
    relevant to an invention for patentability assessment or invalidity analysis.
    """
    return get_prompt("prior_art_search")["content"]


@mcp.prompt()
async def patent_validity_analysis() -> str:
    """Guide for analyzing patent validity and prosecution history.

    USE THIS PROMPT WHEN: You need to assess the strength and validity
    of a patent by reviewing its prosecution history and any challenges.
    """
    return get_prompt("patent_validity")["content"]


@mcp.prompt()
async def competitor_portfolio_analysis() -> str:
    """Guide for analyzing a company's patent portfolio.

    USE THIS PROMPT WHEN: You need to understand a competitor's IP position,
    technology focus areas, and patent strategy.
    """
    return get_prompt("competitor_portfolio")["content"]


@mcp.prompt()
async def ptab_proceeding_research() -> str:
    """Guide for researching PTAB proceedings (IPR/PGR/CBM).

    USE THIS PROMPT WHEN: You need to research Patent Trial and Appeal Board
    proceedings, decisions, and outcomes for validity challenges.
    """
    return get_prompt("ptab_research")["content"]


@mcp.prompt()
async def freedom_to_operate() -> str:
    """Guide for freedom-to-operate (FTO) analysis.

    USE THIS PROMPT WHEN: You need to assess patent infringement risk
    for a product or technology before commercialization.
    """
    return get_prompt("freedom_to_operate")["content"]


@mcp.prompt()
async def patent_landscape() -> str:
    """Guide for patent landscape analysis.

    USE THIS PROMPT WHEN: You need to map the competitive patent environment
    in a technology area to identify trends and opportunities.
    """
    return get_prompt("patent_landscape")["content"]


# =====================================================================
# Diagnostic Tools
# =====================================================================

@mcp.tool()
async def check_api_status() -> Dict[str, Any]:
    """Check status and availability of USPTO ODP API.

    USE THIS TOOL WHEN: You encounter errors or want to verify that the ODP
    is properly configured before starting research.

    Returns status including:
    - Configuration status (API keys, credentials)
    - Connection availability
    - Rate limit information
    """
    status = {
        "odp": {
            "name": "USPTO Open Data Portal",
            "configured": bool(config.USPTO_API_KEY),
            "api_key_set": bool(config.USPTO_API_KEY),
            "note": (
                "ODP (api.uspto.gov) is the primary data source for this server. "
                "Requires a USPTO API key (register at data.uspto.gov)."
            ),
        },
    }

    return {
        "success": True,
        "sources": status,
        "token_budget": {
            "max_response_tokens": config.MAX_RESPONSE_TOKENS,
            "truncation_enabled": config.TRUNCATE_LARGE_RESPONSES,
        }
    }


@mcp.tool()
async def get_cpc_info(cpc_code: str) -> Dict[str, Any]:
    """Look up CPC (Cooperative Patent Classification) code information.

    USE THIS TOOL WHEN: You need to understand what technology area a CPC
    code represents, or find related classification codes.

    Args:
        cpc_code: CPC code to look up (e.g., "G06" for computing, "G06N3/08" for neural networks)

    Returns:
        Classification details including section, title, and description.
        For section codes (A-H, Y), returns subsection list.
    """
    if len(cpc_code) == 1:
        return get_cpc_section_info(cpc_code)
    else:
        return get_cpc_subsection_info(cpc_code)


@mcp.tool()
async def get_status_code(code: str) -> Dict[str, Any]:
    """Look up USPTO application status code meaning.

    USE THIS TOOL WHEN: You encounter a status code in application data
    and need to understand what examination stage it represents.

    Args:
        code: Status code number (e.g., "30" for "Docketed New Case")

    Returns:
        Status code description and examination stage.
    """
    return get_status_code_info(code)


# =====================================================================
# ODP Tools - USPTO Open Data Portal (api.uspto.gov)
# =====================================================================

@mcp.tool()
async def odp_get_application(app_num: str) -> Dict[str, Any]:
    """Get patent application data from USPTO Open Data Portal.

    USE THIS TOOL WHEN: You need prosecution/file wrapper data for an
    application including status, dates, and basic metadata.

    Args:
        app_num: Application number without slashes or commas (e.g., "14412875")

    Returns:
        Application data including filing date, status, and basic info.
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return ResponseEnvelope.from_odp(result)


@mcp.tool()
async def odp_get_application_metadata(app_num: str) -> Dict[str, Any]:
    """Get detailed metadata for a patent application.

    USE THIS TOOL WHEN: You need comprehensive application metadata
    including examiner info, art unit, and detailed status.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/meta-data"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return ResponseEnvelope.from_odp(result)


@mcp.tool()
async def odp_get_continuity(app_num: str) -> Dict[str, Any]:
    """Get patent family/continuity data (parent and child applications).

    USE THIS TOOL WHEN: You need to understand the patent family tree -
    parent applications, continuations, divisionals, and CIPs.

    Args:
        app_num: Application number without slashes (e.g., "14412875")

    Returns:
        Continuity data showing parent/child relationships and priority claims.
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/continuity"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return ResponseEnvelope.from_odp(result)


@mcp.tool()
async def odp_get_assignment(app_num: str) -> Dict[str, Any]:
    """Get patent assignment/ownership records.

    USE THIS TOOL WHEN: You need to know current and historical owners
    of a patent or application.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/assignment"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_adjustment(app_num: str) -> Dict[str, Any]:
    """Get patent term adjustment (PTA) data.

    USE THIS TOOL WHEN: You need to calculate the actual expiration date
    of a patent accounting for USPTO delays.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/adjustment"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_attorney(app_num: str) -> Dict[str, Any]:
    """Get attorney/agent of record for an application.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/attorney"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_foreign_priority(app_num: str) -> Dict[str, Any]:
    """Get foreign priority claims for an application.

    USE THIS TOOL WHEN: You need to find priority claims to foreign
    applications that may affect the effective filing date.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/foreign-priority"
    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_transactions(app_num: str) -> Dict[str, Any]:
    """Get prosecution transaction history for an application.

    USE THIS TOOL WHEN: You need the complete timeline of prosecution
    events including office actions, responses, and fee payments.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/transactions"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_odp(result))


@mcp.tool()
async def odp_get_documents(app_num: str) -> Dict[str, Any]:
    """Get list of documents in the application file wrapper.

    Args:
        app_num: Application number without slashes (e.g., "14412875")
    """
    try:
        app_num = validate_app_number(str(app_num))
    except ValueError as e:
        return ApiError.validation_error(str(e), "app_num")

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/{app_num}/documents"
    result = await api_client.make_request(url)

    if is_error(result):
        return result

    return check_and_truncate(ResponseEnvelope.from_odp(result))


@mcp.tool()
async def odp_search_applications(
    query: Optional[str] = None,
    application_number: Optional[str] = None,
    patent_number: Optional[str] = None,
    inventor_name: Optional[str] = None,
    assignee_name: Optional[str] = None,
    filing_date_from: Optional[str] = None,
    filing_date_to: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Search patent applications in USPTO Open Data Portal.

    USE THIS TOOL WHEN: You need to search applications with filtering
    by applicant metadata, dates, or other criteria not available in PPUBS.

    PARAMETER MAPPING: Simple parameter names are automatically translated to ODP
    Lucene query fields and combined with AND logic:
    - inventor_name → applicationMetaData.firstInventorName
    - assignee_name → applicationMetaData.firstApplicantName
    - patent_number → applicationMetaData.patentNumber
    - application_number → applicationNumberText
    - filing_date_from/to → applicationMetaData.filingDate:[start TO end]

    AUTO-QUOTING & WILDCARDS: Values are automatically quoted for exact phrase
    matching. Use wildcards (*) for partial matches:
    - "Smith" searches for exact phrase
    - "Smit*" searches for anything starting with "Smit"
    - "Micro*" matches "Microsoft", "Microsystems", etc.

    ADVANCED QUERIES: Pass a Lucene-style string in `query` for OR logic or
    raw field names:
    - 'applicationMetaData.firstInventorName:Smith OR
      applicationMetaData.firstInventorName:Jones' for multiple inventors
    - Combine with other filters: query='machine learning' AND assignee_name='IBM'
      generates: (machine learning) AND applicationMetaData.firstApplicantName:"IBM"

    Args:
        query: Free-text or Lucene-style query (e.g., 'neural network',
               'applicationMetaData.firstInventorName:Smith OR Jones')
        application_number: Filter by application number (exact match)
        patent_number: Filter by patent number (exact match)
        inventor_name: Filter by inventor name (matches first inventor; auto-quoted)
        assignee_name: Filter by applicant/assignee name (matches first applicant; auto-quoted)
        filing_date_from: Filing date range start (YYYY-MM-DD)
        filing_date_to: Filing date range end (YYYY-MM-DD)
        offset: Starting position (default: 0)
        limit: Max results (default: 25)
        fields: Response projection — list of ODP field names to return (e.g.,
                ['applicationNumberText', 'applicationMetaData.patentNumber',
                'applicationMetaData.filingDate']). Reduces over-fetching of
                large nested structures. Omit to return all fields (default).

    Returns:
        Normalized response with matching applications.
    """
    clauses: List[str] = []

    def _format_value(value: str) -> str:
        # Escape embedded quotes and backslashes.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')

        # Only quote if the value contains whitespace AND no wildcard chars.
        # This allows wildcards (*, ?) to work as Lucene operators while still
        # quoting multi-word phrases like "Apple Inc."
        has_whitespace = ' ' in value or '\t' in value
        has_wildcard = '*' in value or '?' in value

        if has_whitespace and not has_wildcard:
            # Multi-word phrase without wildcards: quote for exact matching
            return f'"{escaped}"'
        else:
            # Single word, wildcard, or mixed: leave unquoted
            return escaped

    if query:
        # Pass user query through verbatim — supports Lucene operators.
        clauses.append(f"({query})")
    if application_number:
        clauses.append(f"applicationNumberText:{_format_value(application_number)}")
    if patent_number:
        clauses.append(f"applicationMetaData.patentNumber:{_format_value(patent_number)}")
    if inventor_name:
        clauses.append(
            f"applicationMetaData.firstInventorName:{_format_value(inventor_name)}"
        )
    if assignee_name:
        clauses.append(
            f"applicationMetaData.firstApplicantName:{_format_value(assignee_name)}"
        )
    if filing_date_from or filing_date_to:
        start = filing_date_from or "*"
        end = filing_date_to or "*"
        clauses.append(f"applicationMetaData.filingDate:[{start} TO {end}]")

    if not clauses:
        return ApiError.create(
            message=(
                "At least one filter is required. Provide query, "
                "application_number, patent_number, inventor_name, "
                "assignee_name, or a filing_date range."
            ),
            error_code="MISSING_FILTER",
        )

    lucene_query = " AND ".join(clauses)
    body = {
        "q": lucene_query,
        "pagination": {"offset": offset, "limit": limit},
    }

    # Add optional fields parameter for response projection. ODP accepts
    # "fields" as a list in the POST body to limit returned fields.
    if fields and len(fields) > 0:
        body["fields"] = fields

    url = f"{config.API_BASE_URL}/api/v1/patent/applications/search"
    result = await api_client.make_request(url, method="POST", data=body)

    if is_error(result):
        return result

    # Defensive slice in case upstream returns more than requested.
    if isinstance(result, dict) and isinstance(
        result.get("patentFileWrapperDataBag"), list
    ):
        result["patentFileWrapperDataBag"] = (
            result["patentFileWrapperDataBag"][:limit]
        )

    return check_and_truncate(ResponseEnvelope.from_odp(result, offset, limit))


@mcp.tool()
async def odp_search_datasets(
    query: Optional[str] = None,
    offset: int = 0,
    limit: int = 25,
) -> Dict[str, Any]:
    """Search USPTO bulk data products/datasets.

    USE THIS TOOL WHEN: You need to find bulk download datasets
    available from USPTO for large-scale analysis.

    Args:
        query: Search query for dataset names/descriptions
        offset: Starting position (default: 0)
        limit: Max results (default: 25)
    """
    params = {"start": offset, "rows": limit}
    if query:
        params["searchText"] = query

    query_string = api_client.build_query_string(params)
    url = f"{config.API_BASE_URL}/api/v1/datasets/products/search?{query_string}"

    return await api_client.make_request(url)


@mcp.tool()
async def odp_get_dataset(product_id: str) -> Dict[str, Any]:
    """Get details of a specific bulk dataset product.

    Args:
        product_id: Dataset product identifier
    """
    url = f"{config.API_BASE_URL}/api/v1/datasets/products/{product_id}"
    return await api_client.make_request(url)


# =====================================================================
# Main entry point
# =====================================================================

def main():
    """Initialize and run the server with stdio transport."""
    logger.info("Starting ODP Patent MCP server with stdio transport")
    mcp.run(transport='stdio')


if __name__ == "__main__":
    main()
