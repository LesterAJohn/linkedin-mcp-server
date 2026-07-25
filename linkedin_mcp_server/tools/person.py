"""
LinkedIn person profile scraping tools.

Uses innerText extraction for resilient profile data capture
with configurable section selection.
"""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from linkedin_mcp_server.callbacks import MCPContextProgressCallback
from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping import parse_person_sections
from linkedin_mcp_server.scraping.extractor import FilterValidationError

logger = logging.getLogger(__name__)


def register_person_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register all person-related tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Person Profile",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_person_profile(
        linkedin_username: str,
        ctx: Context,
        sections: str | None = None,
        max_scrolls: Annotated[int, Field(ge=1, le=50)] | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Get a specific person's LinkedIn profile.

        Use for profile facts or explicitly selected profile sections. Do not use
        for the authenticated account (use get_my_profile), discovery (use
        search_people), or sidebar recommendations (use get_sidebar_profiles).
        Read-only: navigates LinkedIn and may scroll or expand page content, but
        does not intentionally change account data.

        Requires network access, Patchright Chromium, and an authenticated
        LinkedIn browser profile. The server selects the active profile/runtime;
        this tool has no environment selector. In Docker, create and mount the
        host profile first. Response: {"url": str, "sections": {name: raw_text}}
        with optional references, section_errors, and unknown_sections. Common
        failures include an invalid username/section, private or unavailable
        profile, expired login, rate limiting, browser setup failure, or timeout.
        Use returned references for follow-up entity traversal. Example input:
        {"linkedin_username": "williamhgates", "sections": "experience,skills",
        "max_scrolls": 10}.

        Args:
            linkedin_username: LinkedIn username (e.g., "stickerdaniel", "williamhgates")
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of extra sections to scrape.
                The main profile page is always included.
                Available sections: experience, education, interests, honors, languages, certifications, skills, projects, contact_info, posts
                Examples: "experience,education", "contact_info", "skills,projects", "honors,languages", "posts"
                Default (None) scrapes only the main profile page.
            max_scrolls: Maximum pagination attempts per section to load more content.
                On detail sections (experience, certifications, skills, etc.) this
                is the max number of "Show more" button clicks. On activity/posts
                it is the max scroll-to-bottom iterations. Applies to all sections
                in this call. Default (None) uses 5 for detail sections and 10 for
                posts. Increase when a profile has many items in a section
                (e.g., 30+ certifications, max_scrolls=20). To avoid slowing down
                other sections, request heavy sections in a separate call.

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            Sections may be absent if extraction yielded no content for that page.
            Includes unknown_sections list when unrecognised names are passed.
            The LLM should parse the raw text in each section.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_person_profile"
            )
            requested, unknown = parse_person_sections(sections)

            logger.info(
                "Scraping profile: %s (sections=%s)",
                linkedin_username,
                sections,
            )

            cb = MCPContextProgressCallback(ctx)
            result = await extractor.scrape_person(
                linkedin_username,
                requested,
                callbacks=cb,
                max_scrolls=max_scrolls,
            )

            if unknown:
                result["unknown_sections"] = unknown

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_person_profile")
        except Exception as e:
            raise_tool_error(e, "get_person_profile")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Search People",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "search"},
        exclude_args=["extractor"],
    )
    async def search_people(
        keywords: str,
        ctx: Context,
        location: str | None = None,
        network: list[str] | None = None,
        current_company: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Search for people on LinkedIn.

        Use to discover profiles from keywords and supported people-search
        filters. Do not use when a username is already known (use
        get_person_profile) or for company demographics (use
        get_company_employees). Read-only: submits a LinkedIn search and does not
        intentionally change account data.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        browser profile, and an active profile/runtime selection. This tool has
        no explicit environment selector. Response shape: {"url": str,
        "sections": {"search_results": raw_text}, "references": [...]}.
        Common failures include invalid network codes, a nonnumeric
        current_company value producing unfiltered results, expired login, rate
        limiting, browser failure, or timeout. Call get_company_profile first to
        obtain a company URN, then get_person_profile for a selected result.
        Example input: {"keywords": "software engineer", "location": "New York",
        "network": ["S"], "current_company": "1115"}.

        Args:
            keywords: Search keywords (e.g., "software engineer", "recruiter at Google")
            ctx: FastMCP context for progress reporting
            location: Optional location filter (e.g., "New York", "Remote")
            network: Optional connection-degree filter. Each element is one of
                "F" (1st-degree), "S" (2nd-degree), "O" (3rd-degree and beyond).
                Example: ["F"] to only return 1st-degree connections.
            current_company: Optional current-employer filter. LinkedIn's
                currentCompany facet only filters on the numeric company URN id
                (e.g. "1115" for SAP); plain company names are accepted by the
                URL but ignored by LinkedIn and return the unfiltered result
                set. Look up a company's URN via get_company_profile -- it is
                exposed under references["about"]. For company-wide employee
                demographics (location/education/function breakdown) plus a
                slug-based lookup, use get_company_employees instead.

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text to extract individual people and their profiles.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_people"
            )
            logger.info(
                "Searching people: keywords='%s', location='%s', network=%s, current_company='%s'",
                keywords,
                location,
                network,
                current_company,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Starting people search"
            )

            try:
                result = await extractor.search_people(
                    keywords,
                    location,
                    network=network,
                    current_company=current_company,
                )
            except FilterValidationError as e:
                # Validation messages carry actionable detail; surface
                # them as ToolError so mask_error_details doesn't reduce
                # them to "Error calling tool 'search_people'".
                raise ToolError(str(e)) from e

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except ToolError:
            # Already a properly formatted client-facing error; do not
            # log it as "Unexpected error" via raise_tool_error.
            raise
        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_people")
        except Exception as e:
            raise_tool_error(e, "search_people")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Connect With Person",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"person", "actions"},
        exclude_args=["extractor"],
    )
    async def connect_with_person(
        linkedin_username: str,
        ctx: Context,
        note: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Send a LinkedIn connection request or accept an incoming one.

        Use only when the user explicitly wants to connect with this profile. Do
        not use for profile reading or following. High-risk mutating operation:
        it can send an external invitation or accept an incoming request and
        cannot reliably be undone. Verify the username and note before calling;
        MCP clients should request confirmation because destructiveHint is set.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        profile, and permission to act on that account. The server selects the
        active profile/runtime; this tool has no environment selector. Response:
        {"url": str, "status": str, "message": str, "note_sent": bool}.
        Common failures include unavailable Connect actions, pending/existing
        connections, note quota or note support limits, expired login, rate
        limiting, browser failure, or timeout. Call get_person_profile first to
        verify the recipient. Example input: {"linkedin_username":
        "stickerdaniel", "note": "Thanks for the conversation."}.

        Args:
            linkedin_username: LinkedIn username (e.g., "stickerdaniel", "williamhgates")
            ctx: FastMCP context for progress reporting
            note: Optional note to include with the invitation

        Returns:
            Dict with url, status, message, and note_sent.
            Statuses: pending, already_connected, follow_only,
            connect_unavailable, unavailable, send_failed,
            note_not_supported, custom_note_limit_reached,
            connected, or accepted.

            When status is ``custom_note_limit_reached`` LinkedIn rejected
            personalized invite notes because the free note quota for the
            account is exhausted. The ``message`` is the raw Premium dialog
            text read from LinkedIn.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="connect_with_person"
            )
            logger.info(
                "Connecting with person: %s (note=%s)",
                linkedin_username,
                note is not None,
            )

            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting LinkedIn connection flow",
            )

            result = await extractor.connect_with_person(
                linkedin_username,
                note=note,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "connect_with_person")
        except Exception as e:
            raise_tool_error(e, "connect_with_person")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Sidebar Profiles",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_sidebar_profiles(
        linkedin_username: str,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Get profile links from sidebar recommendation sections on a LinkedIn profile page.

        Use to discover profiles recommended beside one known profile. Do not use
        for keyword search (use search_people) or the target's profile details
        (use get_person_profile). Read-only: navigates recommendation views but
        does not intentionally change account data.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        browser profile, and an active profile/runtime selection. This tool has
        no explicit environment selector. Response shape: {"url": str,
        "sidebar_profiles": {section: ["/in/username/"]}}; absent sections are
        omitted. Common failures include an invalid/private profile, missing or
        Premium-only sidebar sections, expired login, rate limiting, browser
        failure, or timeout. Follow with get_person_profile for selected paths.
        Example input: {"linkedin_username": "williamhgates"}.

        Extracts profiles from "More profiles for you", "Explore premium profiles",
        and "People you may know" sidebar sections. Follows "Show all" links to
        return the full list from each section. Sections that redirect to
        linkedin.com/premium are skipped.

        Args:
            linkedin_username: LinkedIn username of the profile page to scrape
                (e.g., "stickerdaniel", "williamhgates")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url and sidebar_profiles mapping section key to a list of
            /in/username/ paths. Only sections present on the page are included.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_sidebar_profiles"
            )
            logger.info("Getting sidebar profiles for: %s", linkedin_username)

            await ctx.report_progress(
                progress=0, total=100, message="Extracting sidebar profiles"
            )

            result = await extractor.get_sidebar_profiles(linkedin_username)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_sidebar_profiles")
        except Exception as e:
            raise_tool_error(e, "get_sidebar_profiles")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Get My Profile",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_my_profile(
        ctx: Context,
        sections: str | None = None,
        max_scrolls: Annotated[int, Field(ge=1, le=50)] | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Get the authenticated user's own LinkedIn profile.

        Use for the active account's profile and resolved public username. Do not use
        get_person_profile with a guessed username when account identity is the
        goal. Read-only: navigates and may expand profile content but does not
        intentionally change account data.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        browser profile, and an active profile/runtime selection. The
        server-selected active profile/runtime determines which account is
        returned; this tool has no environment selector or user selector.
        Response shape: {"url": str, "sections": {name: raw_text}} with optional
        references, section_errors, and unknown_sections. Common failures include
        missing/expired login, invalid section names, rate limiting, browser
        setup failure, or timeout. Use the resolved username or references in
        follow-up tools. Example input: {"sections": "contact_info,experience",
        "max_scrolls": 10}.

        Navigates to /in/me/ and resolves the redirect to obtain the real
        username before scraping, so the url field in the result is the actual
        profile URL (e.g. linkedin.com/in/johndoe/) rather than /in/me/.

        Args:
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of extra sections to scrape.
                The main profile page is always included.
                Available sections: experience, education, interests, honors, languages, certifications, skills, projects, contact_info, posts
                Examples: "experience,education", "contact_info", "skills,projects"
                Default (None) scrapes only the main profile page.
            max_scrolls: Maximum pagination attempts per section (same as get_person_profile).

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The url field reflects the resolved profile URL, revealing the real username.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_my_profile"
            )
            requested, unknown = parse_person_sections(sections)

            logger.info("Scraping own profile (sections=%s)", sections)

            cb = MCPContextProgressCallback(ctx)
            result = await extractor.get_my_profile(
                sections=requested,
                callbacks=cb,
                max_scrolls=max_scrolls,
            )

            if unknown:
                result["unknown_sections"] = unknown

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_my_profile")
        except Exception as e:
            raise_tool_error(e, "get_my_profile")  # NoReturn
