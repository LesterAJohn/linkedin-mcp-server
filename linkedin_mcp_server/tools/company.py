"""
LinkedIn company profile reading tools.

Uses innerText extraction for resilient company data capture
with configurable section selection.
"""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.callbacks import MCPContextProgressCallback
from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.dependencies import get_ready_extractor, tool_errors
from linkedin_mcp_server.linkedin import parse_company_sections
from linkedin_mcp_server.linkedin.contracts import RATE_LIMITED_SECTION_TEXT
from linkedin_mcp_server.linkedin.contracts import rate_limited_section_error
from linkedin_mcp_server.linkedin.identifiers import (
    company_page_url,
    normalize_company_identifier,
)
from linkedin_mcp_server.linkedin.link_metadata import Reference

logger = logging.getLogger(__name__)


def register_company_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register all company-related tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Company Profile",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"company"},
    )
    @tool_errors
    async def get_company_profile(
        company_name: str,
        ctx: Context,
        sections: str | None = None,
    ) -> dict[str, Any]:
        """
        Get a specific company's LinkedIn profile.

        Use for a known company's about page and optional posts/jobs sections.
        Do not use for company discovery (use search_companies) or employee
        demographics (use get_company_employees). Read-only: navigates LinkedIn
        but does not intentionally change account data.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        browser profile, and the company's LinkedIn URL slug rather than an
        arbitrary display name. The server selects the active profile/runtime;
        this tool has no environment selector. Response: {"url": str,
        "sections": {name: raw_text}} with optional references, section_errors,
        and unknown_sections. Common failures include a wrong slug, unavailable
        company/section, expired login, rate limiting, browser failure, or timeout.
        Call search_companies first when the slug is unknown; use a returned
        company_urn with search_people. Example input: {"company_name": "docker",
        "sections": "posts,jobs"}.

        Args:
            company_name: LinkedIn company name (e.g., "docker", "anthropic", "microsoft"). A full company URL is accepted too and is reduced to the slug.
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of extra sections to read.
                The about page is always included.
                Available sections: posts, jobs
                Examples: "posts", "posts,jobs"
                Default (None) reads only the about page.

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            Includes unknown_sections list when unrecognised names are passed.
            The LLM should parse the raw text in each section.

            When the about section is included, references["about"] may
            include a {kind: "company_urn", value: "<numeric-id>"} entry —
            present whenever the page exposes the "See all employees" link
            (typically all but the smallest companies). The value is the
            numeric id LinkedIn's people-search uses in its currentCompany
            URL facet; plain-text company names are silently ignored by
            that facet.
        """
        # Validate before starting the browser; the reader normalizes the original reference.
        normalize_company_identifier(company_name)
        extractor = await get_ready_extractor(ctx, tool_name="get_company_profile")
        requested, unknown = parse_company_sections(sections)

        logger.info(
            "Reading company: %s (sections=%s)",
            company_name,
            sections,
        )

        cb = MCPContextProgressCallback(ctx)
        result = await extractor.read_company(company_name, requested, callbacks=cb)

        if unknown:
            result["unknown_sections"] = unknown

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Company Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"company"},
    )
    @tool_errors
    async def get_company_posts(
        company_name: str,
        ctx: Context,
        max_scrolls: Annotated[int, Field(ge=1, le=50)] | None = None,
    ) -> dict[str, Any]:
        """
        Get recent posts from a company's LinkedIn feed.

        Use when only a known company's recent posts are needed. Do not use for
        company facts (use get_company_profile) or the authenticated home feed
        (use get_feed). Read-only: navigates the company feed without
        intentionally changing account data.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        browser profile, and the exact company URL slug. The server selects the
        active profile/runtime; this tool has no environment selector. Response:
        {"url": str, "sections": {"posts": raw_text}} with optional
        references.posts and section_errors.posts. Common failures include a
        wrong slug, unavailable posts, expired login, rate limiting, browser
        failure, or timeout. Call search_companies first if the slug is unknown;
        follow returned post references for detail. Example input:
        {"company_name": "microsoft"}.

        Args:
            company_name: LinkedIn company name (e.g., "docker", "anthropic", "microsoft"). A full company URL is accepted too and is reduced to the slug.
            ctx: FastMCP context for progress reporting
            max_scrolls: Maximum scroll-to-bottom iterations to load more posts.
                Default (None) uses 10. Increase to read further back in the feed.

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text to extract individual posts.
        """
        company_name = normalize_company_identifier(company_name)
        extractor = await get_ready_extractor(ctx, tool_name="get_company_posts")
        logger.info("Reading company posts: %s", company_name)

        await ctx.report_progress(
            progress=0, total=100, message="Reading company posts"
        )

        url = company_page_url(company_name, "/posts/")
        extracted = await extractor.extract_page(
            url, section_name="posts", max_scrolls=max_scrolls
        )

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != RATE_LIMITED_SECTION_TEXT:
            sections["posts"] = extracted.text
            if extracted.references:
                references["posts"] = extracted.references
        elif extracted.text == RATE_LIMITED_SECTION_TEXT:
            section_errors["posts"] = rate_limited_section_error()
        elif extracted.error:
            section_errors["posts"] = extracted.error

        await ctx.report_progress(progress=100, total=100, message="Complete")

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Companies",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"company", "search"},
    )
    @tool_errors
    async def search_companies(
        keywords: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """
        Search for companies on LinkedIn.

        Use to discover company pages or resolve a display name to its LinkedIn
        URL slug. Do not use when the slug is already known and profile content
        is required (use get_company_profile). Read-only: submits a LinkedIn
        search without intentionally changing account data.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        browser profile, and an active profile/runtime selection. This tool has
        no explicit environment selector. Response shape: {"url": str,
        "sections": {"search_results": raw_text}, "references": [...]}.
        Common failures include empty/overbroad queries, expired login, rate
        limiting, browser failure, or timeout. Follow with get_company_profile
        or get_company_employees using the selected slug. Example input:
        {"keywords": "electric vehicles"}.

        Args:
            keywords: Search keywords (e.g., "fintech", "anthropic", "electric vehicles")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (search_results -> raw text), and optional references.
            The LLM should parse the raw text to extract individual companies and their pages.
        """
        extractor = await get_ready_extractor(ctx, tool_name="search_companies")
        logger.info("Searching companies: keywords='%s'", keywords)

        await ctx.report_progress(
            progress=0, total=100, message="Starting company search"
        )

        result = await extractor.search_companies(keywords)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Company Employees",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"company"},
    )
    @tool_errors
    async def get_company_employees(
        company_name: str,
        ctx: Context,
        keywords: str | None = None,
    ) -> dict[str, Any]:
        """
        List employees at a company from the LinkedIn /people/ page, including
        the demographics aggregate that this view exposes: where employees
        live, where they studied, and a function breakdown (Engineering, Sales,
        Operations, etc.). The demographics are unique to this tool.

        For filtered search by network degree (1st/2nd/3rd) or location, prefer
        search_people with current_company set to the company URN id. That path
        also returns more result pages than the /people/ tab.

        The optional keywords filter narrows results by name, title, or skill.

        company_name must be the exact LinkedIn URL slug (the path segment after
        /company/), not the display name. LinkedIn assigns unique slugs and the
        display name often does not match. For example, the AI lab Anthropic
        lives at /company/anthropicresearch/, not /company/anthropic/. If you
        are unsure of the slug, call search_companies first and pick the slug
        from the returned references.

        Use for a known company's employee list and aggregate demographics. Do not use
        for network-degree/location filtering; use search_people with the numeric
        company URN instead.

        Read-only: navigates and filters the company people page without
        intentionally changing account data. Requires network access, Patchright
        Chromium, an authenticated LinkedIn profile, and an exact company URL
        slug. The server selects the active profile/runtime; this tool has no
        environment selector. Response: {"url": str, "sections": {"employees":
        raw_text}} with optional employee profile references. Common failures
        include a wrong slug, unavailable people tab, no keyword matches, expired
        login, rate limiting, browser failure, or timeout. Follow references with
        get_person_profile. Example input: {"company_name": "anthropicresearch",
        "keywords": "engineer"}.

        Args:
            company_name: LinkedIn company URL slug (e.g., "docker", "anthropicresearch", "microsoft"). A full company URL is accepted too and is reduced to the slug.
            ctx: FastMCP context for progress reporting
            keywords: Optional filter by name, job title, or skill (e.g., "engineer", "sales")

        Returns:
            Dict with url, sections (employees -> raw text), and optional references.
            References include /in/ profile paths for listed employees.
        """
        # Preserve the reference for the reader's single normalization pass.
        normalize_company_identifier(company_name)
        extractor = await get_ready_extractor(ctx, tool_name="get_company_employees")
        logger.info(
            "Reading company employees: %s (keywords=%s)", company_name, keywords
        )

        await ctx.report_progress(
            progress=0, total=100, message="Loading company employees"
        )

        result = await extractor.get_company_employees(company_name, keywords=keywords)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result
