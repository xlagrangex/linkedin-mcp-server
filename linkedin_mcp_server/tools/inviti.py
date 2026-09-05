"""Bizstudio tools: sent invitations, recent connections, withdraw, structured feed.

These four tools exist for the linkedin14 worker: reconciliation reads two
pages instead of reopening every profile, and the feed comes back as one
record per post with the author's profile URL.
"""

import datetime as dt
import logging
import re
from typing import Annotated, Any
from urllib.parse import quote

from bs4 import BeautifulSoup
from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.dependencies import get_ready_extractor
from linkedin_mcp_server.scraping import inviti_selettori as S

logger = logging.getLogger(__name__)

_RE_BARRIERA = re.compile(r"checkpoint|challenge|captcha", re.I)


def _errore_sessione(e: Exception) -> dict[str, Any]:
    testo = str(e).lower()
    if "captcha" in testo:
        chiave = "captcha"
    elif "checkpoint" in testo or "challenge" in testo or "verif" in testo:
        chiave = "verifica"
    elif "limit" in testo or "restrict" in testo:
        chiave = "avviso_limite"
    else:
        chiave = "login_richiesto"
    return {"errore": chiave, "dettaglio": str(e)[:300]}


async def _scorri(extractor, volte: int) -> None:
    page = extractor._page
    for _ in range(volte):
        await page.mouse.wheel(0, 2400)
        await page.wait_for_timeout(700)


async def _apri(extractor, url: str) -> str:
    await extractor._navigate_to_page(url)
    page = extractor._page
    if _RE_BARRIERA.search(page.url or ""):
        raise AuthenticationError(f"barriera su {page.url}")
    return await page.content()


def register_inviti_tools(mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS) -> None:
    @mcp.tool(timeout=tool_timeout, title="Dump Page HTML",
              annotations={"readOnlyHint": True, "openWorldHint": True}, tags={"bizstudio", "debug"}, exclude_args=["extractor"])
    async def dump_page_html(
        ctx: Context,
        url: str,
        scorri: Annotated[int, Field(ge=0, le=20)] = 3,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Raw ``page.content()`` of a LinkedIn URL after ``scorri`` scrolls. For fixtures only."""
        try:
            extractor = extractor or await get_ready_extractor(ctx, tool_name="dump_page_html")
            await _apri(extractor, url)
            await _scorri(extractor, scorri)
            html = await extractor._page.content()
            return {"url": extractor._page.url, "html": html}
        except AuthenticationError as e:
            return _errore_sessione(e)

    @mcp.tool(timeout=tool_timeout, title="Get Sent Invitations",
              annotations={"readOnlyHint": True, "openWorldHint": True}, tags={"person", "bizstudio"}, exclude_args=["extractor"])
    async def get_sent_invitations(
        ctx: Context,
        max_pages: Annotated[int, Field(ge=1, le=40)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """All pending connection requests sent by the account, from the «Sent invitations» page.

        Returns {voci: [{url, nome, data}], completo: bool}. ``completo`` is false when the
        page could not be read to the end (max_pages reached or a page failed).
        """
        try:
            extractor = extractor or await get_ready_extractor(ctx, tool_name="get_sent_invitations")
            oggi = dt.date.today()
            voci, visti, completo = [], set(), True
            html = await _apri(extractor, S.URL_INVITI)
            for pagina in range(max_pages):
                for v in S.parse_inviti(html, oggi):
                    if v["url"] not in visti:
                        visti.add(v["url"])
                        voci.append(v)
                prima = len(visti)
                await _scorri(extractor, 3)
                html = await extractor._page.content()
                if len(S.parse_inviti(html, oggi)) <= prima and all(v["url"] in visti for v in S.parse_inviti(html, oggi)):
                    break
            else:
                completo = False
            return {"voci": voci, "completo": completo}
        except AuthenticationError as e:
            return _errore_sessione(e)
        except Exception as e:
            logger.exception("get_sent_invitations")
            return {"voci": [], "completo": False, "errore_lettura": str(e)[:300]}

    @mcp.tool(timeout=tool_timeout, title="Get Recent Connections",
              annotations={"readOnlyHint": True, "openWorldHint": True}, tags={"person", "bizstudio"}, exclude_args=["extractor"])
    async def get_recent_connections(
        ctx: Context,
        since: str = "",
        max_pages: Annotated[int, Field(ge=1, le=40)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Connections ordered by date (newest first) from the «Connections» page, until ``since`` (ISO date).

        Returns {voci: [{url, nome, data}], completo: bool}.
        """
        try:
            extractor = extractor or await get_ready_extractor(ctx, tool_name="get_recent_connections")
            oggi = dt.date.today()
            limite = dt.date.fromisoformat(since) if since else oggi - dt.timedelta(days=45)
            voci, visti, completo = [], set(), True
            html = await _apri(extractor, S.URL_COLLEGAMENTI)
            for pagina in range(max_pages):
                nuove = 0
                for v in S.parse_collegamenti(html, oggi):
                    if v["url"] in visti:
                        continue
                    visti.add(v["url"])
                    voci.append(v)
                    nuove += 1
                ultima = voci[-1]["data"] if voci and voci[-1]["data"] else None
                if ultima and dt.date.fromisoformat(ultima) < limite:
                    break
                if nuove == 0 and pagina > 0:
                    break
                await _scorri(extractor, 3)
                html = await extractor._page.content()
            else:
                completo = False
            return {"voci": voci, "completo": completo}
        except AuthenticationError as e:
            return _errore_sessione(e)
        except Exception as e:
            logger.exception("get_recent_connections")
            return {"voci": [], "completo": False, "errore_lettura": str(e)[:300]}

    @mcp.tool(timeout=tool_timeout, title="Withdraw Invitation",
              annotations={"destructiveHint": True, "openWorldHint": True}, tags={"person", "bizstudio", "actions"}, exclude_args=["extractor"])
    async def withdraw_invitation(
        linkedin_url: str,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Withdraw a pending invitation from the «Sent invitations» page. Returns {esito: 'ok' | 'non_trovato'}."""
        try:
            extractor = extractor or await get_ready_extractor(ctx, tool_name="withdraw_invitation")
            url = S.slug_profilo(linkedin_url)
            page = extractor._page
            html = await _apri(extractor, S.URL_INVITI)
            for _ in range(20):
                soup = BeautifulSoup(html, "html.parser")
                btn = S.pulsante_ritira(soup, url)
                if btn is not None:
                    break
                prima = len(S.parse_inviti(html))
                await _scorri(extractor, 3)
                html = await page.content()
                if len(S.parse_inviti(html)) <= prima:
                    return {"esito": "non_trovato"}
            else:
                return {"esito": "non_trovato"}
            slug = url.rsplit("/", 1)[-1]
            card = page.locator(f"li:has(a[href*='/in/{slug}'])").first
            bottone = card.locator("button[aria-label*='ithdraw'], button[aria-label*='itira']").first
            if await bottone.count() == 0:
                bottone = card.locator("button").last
            await bottone.click()
            await page.wait_for_timeout(800)
            conferma = page.locator("[role='dialog'] button[data-test-dialog-primary-btn], [role='alertdialog'] button, [role='dialog'] button").last
            if await conferma.count():
                await conferma.click()
                await page.wait_for_timeout(800)
            return {"esito": "ok"}
        except AuthenticationError as e:
            return _errore_sessione(e)
        except Exception as e:
            logger.exception("withdraw_invitation")
            return {"esito": "non_trovato", "errore_lettura": str(e)[:300]}

    @mcp.tool(timeout=tool_timeout, title="Get Feed Posts",
              annotations={"readOnlyHint": True, "openWorldHint": True}, tags={"feed", "bizstudio"}, exclude_args=["extractor"])
    async def get_feed_posts(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=60)] = 25,
        keywords: str = "",
        extractor: Any | None = None,
    ) -> list[dict[str, Any]]:
        """The home feed as one record per post: {url, autore_url, autore_nome, autore_headline, testo}.

        With ``keywords`` it reads the content search page instead of the feed (the reserve source).
        """
        try:
            extractor = extractor or await get_ready_extractor(ctx, tool_name="get_feed_posts")
            url = S.URL_RICERCA_POST.format(q=quote(keywords)) if keywords else S.URL_FEED
            html = await _apri(extractor, url)
            posts, visti = [], set()
            for _ in range(12):
                for p in S.parse_feed_posts(html):
                    if p["url"] not in visti:
                        visti.add(p["url"])
                        posts.append(p)
                if len(posts) >= limit:
                    break
                await _scorri(extractor, 2)
                html = await extractor._page.content()
            return posts[:limit]
        except AuthenticationError as e:
            return [_errore_sessione(e)]
        except Exception as e:
            logger.exception("get_feed_posts")
            return [{"errore_lettura": str(e)[:300]}]
