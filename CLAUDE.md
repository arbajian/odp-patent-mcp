# CLAUDE.md - Development Guidelines for ODP Patent MCP Server

This file provides guidance for Claude Code and other AI assistants working on this project.

## Project Overview

This is a Model Context Protocol (MCP) server focused exclusively on the USPTO Open Data Portal (ODP) for patent research. The server is built with FastMCP and uses async/await patterns throughout. Published to PyPI as `odp-patent-mcp`.

**Current state (v1.0.0):** 12 ODP tools + 8 diagnostic/resource functions:
- **ODP Tools (12):** Application lookup (6), metadata & relationships (3), documents (1), search (2)
- **Diagnostic/Resource Functions (8):** CPC codes, status codes, data sources, API status checks

## Critical Rules

### Before Committing Changes

**IMPORTANT: Never commit and push changes without ensuring all tests pass.**

```bash
uv run pytest
# Expected: pass/xfail count varies; integration tests skipped by default
```

If tests fail, fix them before committing. Do not skip or delete failing tests unless the functionality has been intentionally removed.

### Release Workflow

When publishing a new version:

1. Run full test suite: `uv run pytest`
2. Bump version in `pyproject.toml` AND `config.py` (USER_AGENT string)
3. Commit and push to `origin/main` (or GitHub new repo)
4. Build: `rm -rf dist/ && uv run python -m build`
5. Publish: `uv run twine upload dist/*`

### Scope: ODP Only

This server is dedicated to ODP. Other data sources (PPUBS, PTAB, PatentsView, etc.) have been removed. When adding features:
- Focus on ODP tools only
- Don't add support for deprecated/unavailable APIs
- If a user needs PPUBS/PTAB data, direct them to the original `patent-mcp-server` package

## Project Structure

```
src/odp_patent_mcp/
├── server.py               # Main server file with all 12 ODP tools + resources
├── __main__.py             # CLI entry point
├── config.py               # Configuration management (environment variables)
├── constants.py            # Constants and enumerations
├── prompts.py              # Workflow prompt templates
├── resources.py            # Static resource data (CPC codes, status codes, ODP metadata)
├── util/
│   ├── response.py         # Response normalization utilities
│   ├── errors.py           # Error handling utilities
│   ├── validation.py       # Input validation with Pydantic
│   └── logging.py          # Logging configuration
└── uspto/
    └── api_uspto_gov.py    # Open Data Portal API client (ODP only)
```

## ODP Tools (12)

### Application Data Retrieval (6 tools)
- `odp_get_application` — Basic application data (status, dates, prosecution stage)
- `odp_get_application_metadata` — Detailed metadata (examiner, art unit, CPC, IPC, dates)
- `odp_get_continuity` — Family tree (parent apps, continuations, divisionals, CIPs)
- `odp_get_assignment` — Ownership history and current assignee
- `odp_get_adjustment` — Patent term adjustment (PTA) for expiration calculation
- `odp_get_attorney` — Attorney/agent of record

### Prosecution & Priority (3 tools)
- `odp_get_foreign_priority` — Foreign priority claims affecting effective filing date
- `odp_get_transactions` — Complete prosecution timeline (office actions, responses, fees)
- `odp_get_documents` — File wrapper document list with download links

### Search & Datasets (2 tools)
- `odp_search_applications` — Query ODP with Lucene syntax, filters, projections
- `odp_get_dataset` — Bulk dataset details and download metadata
- `odp_search_datasets` — Find bulk datasets by name/description

### Diagnostic Tools (3 tools)
- `check_api_status` — Verify ODP connectivity and API health
- `get_cpc_info` / `get_status_code` — CPC and status code reference data
- Resource functions (8) — Expose reference data for workflow context

## Code Conventions

### Function Naming

- **ODP tools**: `odp_*` (e.g., `odp_search_applications`)
- **Diagnostic**: `check_api_status`, `get_cpc_info`, `get_status_code`

### Parameter Naming

- Use `query` not `q` for search queries
- Use `app_num` for application numbers
- Use `patent_number` for patent numbers
- Use `offset` and `limit` for pagination

### Error Handling

All tools return a dictionary with consistent structure:
```python
# Success
{"success": True, "results": [...], "total": N, ...}

# Error
{"error": True, "message": "Error description", "error_code": "CODE"}
```

Use `ApiError.create()` for error responses.

### Async Patterns

All API clients use async/await:
```python
async def tool_name(...) -> Dict[str, Any]:
    result = await api_client.make_request(url)
    if is_error(result):
        return result
    return ResponseEnvelope.from_odp(result)
```

## Dependencies

Managed via `pyproject.toml`. Key dependencies:
- `mcp[cli]` — FastMCP server framework
- `httpx` — Async HTTP client
- `pydantic` — Data validation
- `tenacity` — Retry logic

Dev dependencies include `build` and `twine` for PyPI publishing.

```bash
uv add package-name        # Add dependency
uv sync --dev              # Install dev dependencies
```

## Configuration

Environment variables are loaded from `.env` file:
- `USPTO_API_KEY` — Required for ODP tools
- `LOG_LEVEL` — Logging verbosity (default: INFO)
- `MAX_RESPONSE_TOKENS` — Token limit for response truncation (default: 8000)
- `API_BASE_URL` — ODP base URL (default: https://api.uspto.gov)

See `config.py` for all options.

## Reminders

1. **Always run tests before committing**
2. Keep docstrings up to date — especially "USE THIS TOOL WHEN" guidance
3. Use consistent error handling patterns
4. Follow async patterns
5. Don't introduce new dependencies without good reason
6. Update both `pyproject.toml` version AND `config.py` USER_AGENT on version bumps
7. This server is ODP-only — don't add support for other USPTO data sources
