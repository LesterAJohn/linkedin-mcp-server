# tests/test_action_signals_dom.py
"""Browser-DOM tests for the incoming-request action-row fingerprint.

The unit suite mocks ``page.evaluate``, so the JS in ``_ACTION_SIGNALS_JS``
and ``_CLICK_INCOMING_ACCEPT_JS`` never executes there. These tests run the
real JS against synthetic HTML in headless chromium. Fixtures use German
labels throughout: the fingerprint must classify without reading any label
text. Skipped automatically when chromium is not installed (CI installs no
browser; run locally after ``uv run patchright install chromium``).

Fixture structure mirrors the live DOM dumps of two incoming-request
profiles (2026-06-11): three buttons sharing one parent, Accept and Ignore
carrying aria-label, More carrying aria-expanded without aria-label, plus
sidebar cards with labeled compose anchors and other-user invite anchors.
"""

from __future__ import annotations

import pytest
from patchright.async_api import async_playwright

from linkedin_mcp_server.scraping.extractor import (
    _ACTION_SIGNALS_JS,
    _CLICK_INCOMING_ACCEPT_JS,
)

pytestmark = pytest.mark.browser_dom


INCOMING_TOP_CARD = """
<div class="topcard">
  <h1>Eric Langlouis</h1>
  <div class="actions">
    <button type="button" aria-label="Kontaktanfrage von Eric Langlouis annehmen"
      onclick="document.body.setAttribute('data-clicked','accept')">Annehmen</button>
    <button type="button" aria-label="Kontaktanfrage von Eric Langlouis ignorieren"
      onclick="document.body.setAttribute('data-clicked','ignore')">Ignorieren</button>
    <button type="button" aria-expanded="false">Mehr</button>
  </div>
</div>
"""

SIDEBAR_CARDS = """
<aside class="sidebar">
  <div class="card">
    <a href="https://www.linkedin.com/in/julien-f/">Julien</a>
    <a href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3AAAA"
      aria-label="Nachricht an Julien senden">Nachricht</a>
  </div>
  <div class="card">
    <a href="https://www.linkedin.com/in/rahul-g/">Rahul</a>
    <a href="/preload/custom-invite/?vanityName=rahul-g"
      aria-label="Rahul als Kontakt einladen">Vernetzen</a>
  </div>
  <button type="button">Mehr anzeigen</button>
</aside>
"""

VIDEO_PLAYER_BAR = """
<div class="player">
  <button type="button" aria-label="Abspielen">▶</button>
  <button type="button" aria-label="Stummschalten">🔇</button>
  <button type="button" aria-label="Untertitel">CC</button>
  <button type="button" aria-label="Vollbild">⛶</button>
  <button type="button" aria-expanded="false" aria-label="Einstellungen">⚙</button>
</div>
"""

CONNECTED_TOP_CARD = """
<div class="topcard">
  <h1>Fadi Al Eliwi</h1>
  <div class="actions">
    <a href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3ABBB"
      aria-disabled="false">Nachricht</a>
    <button type="button" aria-expanded="false">Mehr</button>
  </div>
</div>
"""

FOLLOW_ONLY_TOP_CARD = """
<div class="topcard">
  <h1>Verena</h1>
  <div class="actions">
    <button type="button" aria-label="Verena folgen">Folgen</button>
    <a href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3ACCC">Nachricht</a>
    <button type="button" aria-expanded="false">Mehr</button>
  </div>
</div>
"""

PENDING_TOP_CARD = """
<div class="topcard">
  <h1>Florian</h1>
  <div class="actions">
    <a href="/messaging/compose/?profileUrn=urn%3Ali%3Afsd_profile%3ADDD">Nachricht</a>
    <a href="https://www.linkedin.com/in/florian/"
      aria-label="Ausstehend, klicken zum Zurückziehen">Ausstehend</a>
    <button type="button" aria-expanded="false">Mehr</button>
  </div>
</div>
"""

EXPANDER_FIRST_BAR = """
<div class="hostile">
  <button type="button" aria-expanded="false">⚙</button>
  <button type="button" aria-label="Aktion A">A</button>
  <button type="button" aria-label="Aktion B">B</button>
</div>
"""

EXTRA_BUTTON_ROW = """
<div class="hostile">
  <button type="button" aria-label="Aktion A">A</button>
  <button type="button" aria-label="Aktion B">B</button>
  <button type="button" aria-expanded="false">Mehr</button>
  <button type="button">Extra</button>
</div>
"""


def _page_html(*fragments: str) -> str:
    return f"<html><body><main>{''.join(fragments)}</main></body></html>"


@pytest.fixture
async def dom_page():
    """Real chromium page, or skip when no browser is installed."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            yield page
            await browser.close()
    except Exception as exc:  # browser binary missing
        pytest.skip(f"chromium unavailable: {exc}")


async def _signals(page, html: str) -> dict:
    await page.set_content(html)
    return await page.evaluate(_ACTION_SIGNALS_JS, "testuser")


class TestIncomingActionRowFingerprint:
    async def test_incoming_row_detected_next_to_sidebar_cards(self, dom_page):
        data = await _signals(dom_page, _page_html(INCOMING_TOP_CARD, SIDEBAR_CARDS))
        assert data["hasIncomingActionRow"] is True

    async def test_video_player_bar_not_detected(self, dom_page):
        data = await _signals(
            dom_page, _page_html(CONNECTED_TOP_CARD, VIDEO_PLAYER_BAR)
        )
        assert data["hasIncomingActionRow"] is False

    async def test_expander_first_order_guard(self, dom_page):
        data = await _signals(dom_page, _page_html(EXPANDER_FIRST_BAR))
        assert data["hasIncomingActionRow"] is False

    async def test_preceding_nonmatching_expander_does_not_abort_scan(self, dom_page):
        # Cover-video layout: the player's expander renders before the
        # top-card row in DOM order; the scan must continue past it.
        data = await _signals(dom_page, _page_html(VIDEO_PLAYER_BAR, INCOMING_TOP_CARD))
        assert data["hasIncomingActionRow"] is True

    async def test_extra_unlabeled_button_fails_count_guard(self, dom_page):
        data = await _signals(dom_page, _page_html(EXTRA_BUTTON_ROW))
        assert data["hasIncomingActionRow"] is False

    async def test_follow_only_row_not_detected(self, dom_page):
        data = await _signals(dom_page, _page_html(FOLLOW_ONLY_TOP_CARD))
        assert data["hasIncomingActionRow"] is False

    async def test_pending_row_not_detected(self, dom_page):
        data = await _signals(dom_page, _page_html(PENDING_TOP_CARD))
        assert data["hasIncomingActionRow"] is False

    async def test_connected_row_not_detected(self, dom_page):
        data = await _signals(dom_page, _page_html(CONNECTED_TOP_CARD, SIDEBAR_CARDS))
        assert data["hasIncomingActionRow"] is False


class TestClickIncomingAccept:
    async def test_clicks_first_labeled_button_only(self, dom_page):
        await dom_page.set_content(_page_html(INCOMING_TOP_CARD, SIDEBAR_CARDS))
        clicked = await dom_page.evaluate(_CLICK_INCOMING_ACCEPT_JS)
        assert clicked is True
        # Patchright evaluates in an isolated world; page-world variables
        # are invisible there, but the DOM is shared — the inline onclick
        # records the click as a body attribute.
        recorded = await dom_page.evaluate("document.body.getAttribute('data-clicked')")
        assert recorded == "accept"

    async def test_no_click_without_fingerprint_match(self, dom_page):
        await dom_page.set_content(_page_html(FOLLOW_ONLY_TOP_CARD, VIDEO_PLAYER_BAR))
        clicked = await dom_page.evaluate(_CLICK_INCOMING_ACCEPT_JS)
        assert clicked is False
