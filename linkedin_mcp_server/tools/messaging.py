"""
LinkedIn messaging tools.

Provides inbox listing, conversation reading, message search, and sending.
"""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    LinkedInScraperException,
)
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error

logger = logging.getLogger(__name__)


def register_messaging_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register all messaging-related tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Inbox",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_inbox(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        List recent conversations from the LinkedIn messaging inbox.

        Use for an inbox overview before selecting a thread. Do not use for
        keyword filtering (use search_conversations) or a full thread body (use
        get_conversation). Read-only: scrolls the inbox without intentionally
        opening rows or changing message state.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        profile with messaging access, and an active profile/runtime selection.
        This tool has no explicit environment selector; it uses the server's
        active profile/runtime. Response shape: {"url": str, "sections":
        {"inbox": raw_text}, "conversation_counts": {"read": int, "unread": int,
        "unknown": int, "total": int}, "conversations": [
        {"participant": str, "aria_label": str, "read_state": "read"|"unread"|"unknown",
        "thread_id": str|None, "thread_url": str|None}
        ], "references": [...]}. Common failures include limit outside 1-50,
        restricted messaging access, expired login, rate limiting, browser
        failure, or timeout. Follow with get_conversation using a returned
        thread_id when available. Example input: {"limit": 20}.

        Args:
            ctx: FastMCP context for progress reporting
            limit: Maximum number of conversations to load (1-50, default 20)

        Returns:
            Dict with url, sections (inbox -> raw text), conversation_counts,
            conversations, and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_inbox"
            )
            logger.info("Fetching inbox (limit=%d)", limit)

            await ctx.report_progress(
                progress=0, total=100, message="Loading messaging inbox"
            )

            result = await extractor.get_inbox(limit=limit)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_inbox")
        except Exception as e:
            raise_tool_error(e, "get_inbox")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Conversation",
        # Not read-only, though it reads: resolving a username enumerates the
        # inbox by click-visiting rows, and LinkedIn marks a visited row as read.
        # The docstring below has always said so. An unread message the user has
        # not seen is state, and losing it is not something a reader should do.
        annotations={"readOnlyHint": False, "openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_conversation(
        ctx: Context,
        linkedin_username: str | None = None,
        thread_id: str | None = None,
        index: Annotated[int, Field(ge=0)] = 0,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Read a specific messaging conversation.

        Provide either linkedin_username or thread_id to identify the conversation.

        Use for full thread content after resolving a participant or thread ID.
        Do not use to send a reply (use reply_message). Low-risk mutating read:
        opening or enumerating matching rows can mark messages as read. Prefer a
        known thread_id to minimize UI visits; this tool does not send messages.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        profile with access to the conversation, and an active profile/runtime
        selection. This tool has no explicit environment selector. Response shape:
        {"url": str, "sections": {"conversation": raw_text}, "references": [...]}. Common
        failures include providing neither identifier, an invalid/inaccessible
        thread, ambiguous participant matches, index outside available matches,
        expired login, rate limiting, browser failure, or timeout. Call
        search_conversations to enumerate thread IDs, then reply_message only
        after user approval. Example input: {"thread_id": "2-abc123"}.

        When looked up by linkedin_username, resolution searches the messaging
        inbox for the participant's display name and click-visits every matching
        row to capture its thread ID — LinkedIn's sidebar has no anchor hrefs or
        thread-id attributes, so this is the only available path. Each visit
        selects the row in the LinkedIn UI and may mark it as read. Pass
        thread_id directly to skip this enumeration.

        Args:
            ctx: FastMCP context for progress reporting
            linkedin_username: LinkedIn username of the conversation participant
            thread_id: LinkedIn messaging thread ID
            index: 0-based selector for which thread to open when the
                participant has multiple threads (e.g. an organic 1-on-1 plus
                an InMail). Ignored when thread_id is provided. To enumerate
                thread IDs first, call search_conversations.

        Returns:
            Dict with url, sections (conversation -> raw text), and optional references.
        """
        if not linkedin_username and not thread_id:
            raise_tool_error(
                LinkedInScraperException(
                    "Provide at least one of linkedin_username or thread_id"
                ),
                "get_conversation",
            )

        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_conversation"
            )
            logger.info(
                "Fetching conversation: username=%s, thread_id=%s, index=%d",
                linkedin_username,
                thread_id,
                index,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Loading conversation"
            )

            result = await extractor.get_conversation(
                linkedin_username=linkedin_username,
                thread_id=thread_id,
                index=index,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_conversation")
        except Exception as e:
            raise_tool_error(e, "get_conversation")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Conversations",
        # Same reason as `get_conversation`: enumerating result rows selects them
        # in LinkedIn's UI, which can mark them read. Its own `limit` argument is
        # documented in those terms.
        annotations={"readOnlyHint": False, "openWorldHint": True},
        tags={"messaging", "search"},
        exclude_args=["extractor"],
    )
    async def search_conversations(
        keywords: str,
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Search messages by keyword.

        Use to find existing threads and enumerate thread IDs by message text or
        participant. Do not use for an unfiltered inbox (use get_inbox) or to send
        content. Low-risk mutating read: enumerating results selects rows in the
        LinkedIn UI and may mark conversations as read; keep limit small.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        profile with messaging access, and an active profile/runtime selection.
        This tool has no explicit environment selector. Response shape: {"url": str,
        "sections": {"search_results": raw_text}, "references": [...]}. Common
        failures include an empty/noisy query, limit outside 1-50, expired login,
        rate limiting, browser failure, or timeout. Follow with get_conversation
        or, after user confirmation, reply_message. Example input:
        {"keywords": "project atlas", "limit": 10}.

        Args:
            keywords: Search keywords to filter conversations
            ctx: FastMCP context for progress reporting
            limit: Maximum number of search-result rows to enumerate as
                conversation references (1-50, default 20). Each enumeration
                selects the row in LinkedIn's UI and may mark it as read, so
                a low cap is preferable for noisy queries.

        Returns:
            Dict with url, sections (search_results -> raw text), and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_conversations"
            )
            logger.info(
                "Searching conversations: keywords='%s', limit=%d", keywords, limit
            )

            await ctx.report_progress(
                progress=0, total=100, message="Searching messages"
            )

            result = await extractor.search_conversations(keywords, limit=limit)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_conversations")
        except Exception as e:
            raise_tool_error(e, "search_conversations")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Send Message",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"messaging", "actions"},
        exclude_args=["extractor"],
    )
    async def send_message(
        linkedin_username: str,
        message: str,
        confirm_send: bool,
        ctx: Context,
        profile_urn: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to a LinkedIn user.

        The recipient must be directly messageable from the profile page. This is a
        write operation when confirm_send is True.

        Use to start or continue a profile-addressed conversation when the
        recipient username is known. Do not use for a strict existing-thread reply
        (use reply_message). High-risk mutating operation: confirm_send=true sends
        external content immediately and cannot reliably be undone. First call
        with confirm_send=false to verify composer/recipient resolution; review
        the exact recipient and message before the confirmed call.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        profile with messaging permission, and user authorization to send. The
        server selects the active profile/runtime; this tool has no environment
        selector. Response: {"url": str, "status": str, "message": str,
        "recipient_selected": bool, "sent": bool}. Common failures include an
        unavailable Message action, recipient mismatch, invalid profile URN,
        unavailable composer, expired login, rate limiting, browser failure, or
        timeout. Call get_person_profile to verify username/profile_urn and
        search_conversations afterward if inbox display lags. Example input:
        {"linkedin_username": "stickerdaniel", "message": "Hello!",
        "confirm_send": false}.

        Args:
            linkedin_username: LinkedIn username of the recipient
            message: The message text to send
            confirm_send: Must be True to send the message
            ctx: FastMCP context for progress reporting
            profile_urn: Optional profile URN (e.g. ACoAAB...) to construct the
                compose URL directly. Providing this bypasses the Message-button
                lookup and is more reliable when available. Obtain via
                get_person_profile. Note: inbox may not always show all
                messages; use search_conversations as a fallback.

        Returns:
            Dict with url, status, message, recipient_selected, and sent.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="send_message"
            )
            logger.info(
                "Sending message to %s (confirm_send=%s)",
                linkedin_username,
                confirm_send,
            )

            await ctx.report_progress(progress=0, total=100, message="Sending message")

            result = await extractor.send_message(
                linkedin_username,
                message,
                confirm_send=confirm_send,
                profile_urn=profile_urn,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "send_message")
        except Exception as e:
            raise_tool_error(e, "send_message")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Reply Message",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"messaging", "actions"},
        exclude_args=["extractor"],
    )
    async def reply_message(
        thread_id: str,
        message: str,
        confirm_send: bool,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Reply in an existing conversation identified by thread_id.

        This tool requires ``thread_id`` and never uses recipient compose flows,
        so it cannot intentionally start a new conversation thread.

        Use only for a reply to a verified existing thread. Do not use when only a
        username is known (resolve the thread with search_conversations first).
        High-risk mutating operation: confirm_send=true sends external content
        immediately and cannot reliably be undone. First call with
        confirm_send=false to verify that the thread and composer resolve; review
        the exact thread and message before the confirmed call.

        Requires network access, Patchright Chromium, an authenticated LinkedIn
        profile with access to the thread, and user authorization to send. The
        server selects the active profile/runtime; this tool has no environment
        selector. Response: {"url": str, "status": str, "message": str,
        "recipient_selected": bool, "sent": bool}. Common failures include an
        empty/invalid thread ID, thread mismatch, unavailable composer, expired
        login, rate limiting, browser failure, or timeout. Use get_conversation to
        verify context before replying. Example input: {"thread_id": "2-abc123",
        "message": "Thanks, I will follow up tomorrow.", "confirm_send": false}.

        Args:
            thread_id: LinkedIn messaging thread ID (required)
            message: The reply text to send
            confirm_send: Must be True to send the reply
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, status, message, recipient_selected, and sent.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="reply_message"
            )
            logger.info(
                "Replying in thread %s (confirm_send=%s)",
                thread_id,
                confirm_send,
            )

            await ctx.report_progress(progress=0, total=100, message="Replying")

            result = await extractor.reply_message(
                thread_id,
                message,
                confirm_send=confirm_send,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "reply_message")
        except Exception as e:
            raise_tool_error(e, "reply_message")  # NoReturn
