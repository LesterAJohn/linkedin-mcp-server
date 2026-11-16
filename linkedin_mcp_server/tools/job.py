"""
LinkedIn job reading tools with search and detail extraction.

Uses innerText extraction for resilient job data capture.
"""

import logging
import time
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.dependencies import get_ready_extractor, tool_errors
from linkedin_mcp_server.linkedin.identifiers import normalize_job_id
from linkedin_mcp_server.linkedin.job_policy import JobsTrackerStage

logger = logging.getLogger(__name__)


def register_job_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register all job-related tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Job Details",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job"},
    )
    @tool_errors
    async def get_job_details(
        job_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """
        Get job details for a specific job posting on LinkedIn.

        Use when a numeric LinkedIn job ID is known and full posting text is
        needed. Do not use for discovery (use search_jobs). Read-only: navigates
        the posting without intentionally changing account data or applying.

        Requires network access, Patchright Chromium, and an authenticated
        LinkedIn browser profile. The server selects the active profile/runtime;
        this tool has no environment selector. Response: {"url": str,
        "sections": {name: raw_text}} with optional references and
        section_errors. Common failures include a malformed, expired, removed, or
        inaccessible job ID, expired login, rate limiting, browser failure, or
        timeout. Obtain IDs from search_jobs. Example input:
        {"job_id": "4252026496"}.

        Args:
            job_id: LinkedIn job ID (e.g., "4252026496", "3856789012")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text to extract job details. Jobs in
            the posting's "More jobs" list are references with context
            "similar job"; their ids work with get_job_details.
            section_errors.job_posting.error_type "description_missing" means
            the captured text lacks the expected "About the job" heading.
            The text is kept but may be incomplete; calling again may return more.
            A posting that shows it was applied to or has closed also carries
            apply: {type: "applied" | "closed"}; no apply key means neither
            was read, not that the posting is open.
        """
        job_id = normalize_job_id(job_id)
        extractor = await get_ready_extractor(ctx, tool_name="get_job_details")
        logger.info("Reading job: %s", job_id)

        await ctx.report_progress(
            progress=0, total=100, message="Reading the job posting"
        )

        result = await extractor.read_job(job_id)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Job Apply URL",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job"},
    )
    @tool_errors
    async def get_job_apply_url(
        job_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """
        Get how a job posting takes applications, and the employer's application link.

        Use when the job ID is known and only its application route is needed. Do
        not use for the full posting (use get_job_details) or to read an Easy
        Apply form (use get_easy_apply_form). Requires network access, Patchright
        Chromium, and an authenticated LinkedIn browser profile; this tool has no
        environment selector. Common failures include an invalid or inaccessible
        job ID, expired login, rate limiting, browser failure, or timeout. Example
        input: {"job_id": "4252026496"}. Response shape:
        {"url": str, "apply": {"type": str, "url"?: str}}.

        Args:
            job_id: LinkedIn job ID (e.g., "4252026496", "3856789012")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url and apply: {type, url?}. type is easy_apply,
            external, applied, closed or unknown. url is the employer's
            application link as LinkedIn gives it, for external postings;
            it is not opened, so a short link is returned unexpanded.
            A posting that could not be read returns section_errors instead.
        """
        job_id = normalize_job_id(job_id)
        extractor = await get_ready_extractor(ctx, tool_name="get_job_apply_url")
        logger.info("Reading apply link: %s", job_id)

        await ctx.report_progress(progress=0, total=100, message="Opening job posting")

        result = await extractor.get_job_apply_url(job_id)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Easy Apply Form",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        tags={"job"},
    )
    @tool_errors
    async def get_easy_apply_form(
        job_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """
        Read the questions of a job posting's Easy Apply form, without answering them.

        Opens Easy Apply and reads each step: every field's label, type, whether
        it is required, its options, character limit and prefilled value. Next
        is clicked only while LinkedIn accepts a step on its prefilled values,
        so the first step that needs input ends the read. Nothing is typed,
        Review and Submit are never clicked, and the dialog is closed with its
        draft discarded.

        Use only to inspect a known job's Easy Apply questions. Do not use to
        submit an application or when only the posting or external application
        link is needed (use get_job_details or get_job_apply_url). Requires
        network access, Patchright Chromium, and an authenticated LinkedIn
        browser profile; this tool has no environment selector. Common failures
        include an invalid or inaccessible job ID, expired login, rate limiting,
        browser failure, or a draft that could not be discarded. Example input:
        {"job_id": "4252026496"}. Response shape:
        {"url": str, "apply": {"type": str}, "form": {"steps": [...], "complete": bool}}.

        Args:
            job_id: LinkedIn job ID (e.g., "4252026496", "3856789012")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url and apply: {type} (easy_apply, external, applied,
            closed or unknown). Easy Apply postings add form: {steps, complete},
            each step {heading?, progress?, fields}. complete is false when
            steps after the last one were not read; the fields LinkedIn refused
            on that step carry its error. section_errors reports a form that
            could not be read or a draft that could not be discarded.
        """
        job_id = normalize_job_id(job_id)
        extractor = await get_ready_extractor(ctx, tool_name="get_easy_apply_form")
        logger.info("Reading Easy Apply form: %s", job_id)

        await ctx.report_progress(progress=0, total=100, message="Opening job posting")

        result = await extractor.get_easy_apply_form(job_id)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Jobs",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job", "search"},
    )
    @tool_errors
    async def search_jobs(
        keywords: str,
        ctx: Context,
        location: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        date_posted: str | None = None,
        job_type: str | None = None,
        experience_level: str | None = None,
        work_type: str | None = None,
        easy_apply: bool = False,
        sort_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Search for jobs on LinkedIn.

        Returns job_ids that can be passed to get_job_details for full info.

        Use for job discovery with LinkedIn's supported filters. Do not use when
        a job ID is already known (use get_job_details). Requires network access,
        Patchright Chromium, and an authenticated LinkedIn browser profile. The
        active profile is selected by the server; this tool has no environment
        selector. Common failures include unsupported filters, invalid paging,
        expired login, rate limiting, browser failure, or timeout. Example input:
        {"keywords": "data scientist", "location": "Remote", "max_pages": 2}.
        Response: {"url": str, "sections": {name: raw_text}, "job_ids": [...]}.

        LinkedIn is moving accounts to a redesigned job search, which keeps
        only the keywords, date_posted and paging. It drops the filters
        marked "Classic search only" and reports them in section_errors as
        filters_dropped. When it drops the location or work type, search again
        with them in the keywords ("remote python developer in Lyon") and keep
        doing so; words such as "senior" or "internship" there narrow the
        results too. Quotes do not make a phrase exact on that search.

        Args:
            keywords: Search keywords (e.g., "software engineer", "data scientist")
            ctx: FastMCP context for progress reporting
            location: Optional location filter (e.g., "San Francisco", "Remote"). Classic search only.
            max_pages: Maximum number of result pages to load (1-10, default 3)
            date_posted: Filter by posting date (past_hour, past_24_hours, past_week, past_month)
            job_type: Filter by job type, comma-separated (full_time, part_time, contract, temporary, volunteer, internship, other). Classic search only.
            experience_level: Filter by experience level, comma-separated (internship, entry, associate, mid_senior, director, executive). Classic search only.
            work_type: Filter by work type, comma-separated (on_site, remote, hybrid). Classic search only.
            easy_apply: Only show Easy Apply jobs (default false). Classic search only.
            sort_by: Sort results (date, relevance). Classic search only.

        Returns:
            Dict with url, sections (name -> raw text), job_ids (list of
            numeric job ID strings usable with get_job_details), and optional references.
            total ({count, exact}) is the result count LinkedIn advertises,
            with exact false for a lower bound such as "1,000+".
            promoted_job_ids is the subset of job_ids LinkedIn marks as
            promoted, present only when every page could be read.
            A search with no matches returns empty job_ids and a
            section_errors entry of type no_matching_jobs, rather than the
            unrelated recommendations LinkedIn shows in its place.
            pagination ({pages_fetched, next_offset, reason}) says why paging
            stopped: complete (the results ran out), max_pages, time_budget
            (the tool timeout was near, so more results may exist) or
            no_new_ids (a page repeated earlier results). It is absent when a
            failure stopped the search, which section_errors then explains.
        """
        # Before the browser, because FastMCP is already timing this call
        # and the extractor's budget is a fraction of the same figure. A
        # cold start that spends three of ten seconds left it planning
        # against eight it no longer had, and the call was cancelled with
        # every page it had gathered.
        started = time.monotonic()
        extractor = await get_ready_extractor(ctx, tool_name="search_jobs")
        logger.info(
            "Searching jobs: keywords='%s', location='%s', max_pages=%d",
            keywords,
            location,
            max_pages,
        )

        await ctx.report_progress(progress=0, total=100, message="Starting job search")

        result = await extractor.search_jobs(
            keywords,
            location=location,
            max_pages=max_pages,
            date_posted=date_posted,
            job_type=job_type,
            experience_level=experience_level,
            work_type=work_type,
            easy_apply=easy_apply,
            sort_by=sort_by,
            # What is left of the figure FastMCP cancels this call on.
            tool_timeout=max(0.0, tool_timeout - (time.monotonic() - started)),
        )

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Saved Jobs",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job"},
    )
    @tool_errors
    async def get_saved_jobs(
        ctx: Context,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        stage: JobsTrackerStage = "saved",
    ) -> dict[str, Any]:
        """
        List the authenticated user's jobs at one stage of LinkedIn's job tracker.

        Returns job_ids that can be passed to get_job_details for full info.

        Use when checking the user's saved or tracked applications. Do not use
        for job discovery (use search_jobs) or posting details (use
        get_job_details). Requires network access, Patchright Chromium, and an
        authenticated LinkedIn browser profile; this tool has no environment
        selector. Common failures include unsupported tracker stages, invalid
        paging, expired login, rate limiting, browser failure, or timeout.
        Example input: {"stage": "saved", "max_pages": 2}. Response shape:
        {"url": str, "sections": {name: raw_text}, "job_ids": [...]}.

        Args:
            ctx: FastMCP context for progress reporting
            max_pages: Maximum number of tracker pages to load (1-10, default 3)
            stage: Tracker tab: saved (default), in_progress, applied or
                archived. applied lists Easy Apply submissions and external
                applications the user confirmed to LinkedIn.

        Returns:
            Dict with url, sections (name -> raw text), job_ids (list of
            numeric job ID strings usable with get_job_details), and optional references.
        """
        extractor = await get_ready_extractor(ctx, tool_name="get_saved_jobs")
        logger.info("Fetching %s jobs (max_pages=%d)", stage, max_pages)

        await ctx.report_progress(progress=0, total=100, message="Loading saved jobs")

        result = await extractor.get_saved_jobs(max_pages=max_pages, stage=stage)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result

    @mcp.tool(
        timeout=tool_timeout,
        title="Dismiss Job",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        tags={"job"},
    )
    @tool_errors
    async def dismiss_job(
        job_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """
        Dismiss a job from LinkedIn job search so later searches never show it.

        For jobs already applied to or rejected. Reads the job's title and
        company from its posting, clicks the dismiss button on its card in a
        worldwide search for them, then reloads that search to confirm the job
        is gone. Irreversible: LinkedIn offers no undo after a reload. A job
        that search does not list on its first page is an error, since an
        already dismissed job and a search that missed it look the same.

        Use only when the user explicitly asks to dismiss this job. Do not use
        to save a job or change its application status. Requires network access,
        Patchright Chromium, and an authenticated LinkedIn browser profile; this
        tool has no environment selector. Common failures include an invalid or
        inaccessible job ID, the job not appearing in the first search page,
        expired login, rate limiting, browser failure, or timeout. Example
        input: {"job_id": "4252026496"}. Response shape:
        {"url": str, "job_id": str, "dismissed": bool}.

        Args:
            job_id: LinkedIn job ID (e.g., "4252026496", "3856789012")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url (the search), job_id, keywords (the searched title
            and company), dismissed (true) and already_dismissed, true when
            the card was already dismissed.
        """
        job_id = normalize_job_id(job_id)
        extractor = await get_ready_extractor(ctx, tool_name="dismiss_job")
        logger.info("Dismissing job %s", job_id)

        await ctx.report_progress(progress=0, total=100, message="Opening job posting")

        result = await extractor.dismiss_job(job_id)

        await ctx.report_progress(progress=100, total=100, message="Complete")

        return result
