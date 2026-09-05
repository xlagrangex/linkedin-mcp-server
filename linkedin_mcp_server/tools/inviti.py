"""Bizstudio tools: sent invitations, recent connections, withdraw, structured feed.

These four tools exist for the linkedin14 worker: reconciliation reads two
pages instead of reopening every profile, and the feed comes back as one
record per post with the author's profile URL.
"""

import asyncio
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
from linkedin_mcp_server.scraping.extractor import _POST_SLUG_URL_RE

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


_CARICA_ALTRO = "button:has-text('Carica altro'), button:has-text('Load more'), button:has-text('Mostra altri'), button:has-text('Show more results')"


async def _carica_altro(extractor) -> bool:
    """Le pagine inviti/collegamenti del 2026 paginano con un bottone, non a scroll."""
    page = extractor._page
    await _scorri(extractor, 1)
    btn = page.locator(_CARICA_ALTRO).first
    try:
        if await btn.count() and await btn.is_visible():
            await btn.click()
            await page.wait_for_timeout(1500)
            return True
    except Exception:
        logger.debug("carica altro non cliccabile", exc_info=True)
    return False


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
        cattura: str = "",
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Raw ``page.content()`` of a LinkedIn URL after ``scorri`` scrolls. For fixtures only.

        With ``cattura`` (a regex) every network response body is searched and the matches
        are returned per response URL, so we can see which payload carries which data.
        """
        try:
            extractor = extractor or await get_ready_extractor(ctx, tool_name="dump_page_html")
            page = extractor._page
            catture: list[dict[str, Any]] = []
            attese: list[Any] = []
            rx = re.compile(cattura) if cattura else None

            def _on_response(resp):
                async def _leggi():
                    try:
                        corpo = (await resp.body()).decode("utf-8", errors="replace")
                    except Exception:
                        return
                    trovati = [m.group(0)[:200] for m in rx.finditer(corpo)]
                    if trovati:
                        catture.append({"url": resp.url[:200], "n": len(trovati), "esempi": trovati[:8]})
                attese.append(asyncio.create_task(_leggi()))

            if rx:
                page.on("response", _on_response)
            try:
                await _apri(extractor, url)
                await _scorri(extractor, scorri)
                html = await page.content()
            finally:
                if rx:
                    page.remove_listener("response", _on_response)
                    if attese:
                        await asyncio.gather(*attese, return_exceptions=True)
            return {"url": page.url, "html": html, "catture": catture}
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
                if not await _carica_altro(extractor):
                    break
                html = await extractor._page.content()
                if all(v["url"] in visti for v in S.parse_inviti(html, oggi)):
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
                if not await _carica_altro(extractor):
                    break
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
            for _ in range(40):
                soup = BeautifulSoup(html, "html.parser")
                if S.pulsante_ritira(soup, url) is not None:
                    break
                if not await _carica_altro(extractor):
                    return {"esito": "non_trovato"}
                html = await page.content()
            else:
                return {"esito": "non_trovato"}
            slug = url.rsplit("/", 1)[-1]
            card = page.locator(f"div[role='listitem']:has(a[href*='/in/{slug}']), li:has(a[href*='/in/{slug}'])").first
            bottone = card.locator("a[aria-label*='itira'], a[aria-label*='ithdraw'], button[aria-label*='ithdraw'], button[aria-label*='itira']").first
            if await bottone.count() == 0:
                return {"esito": "non_trovato", "errore_lettura": "controllo Ritira non trovato nella card"}
            await bottone.click()
            await page.wait_for_timeout(1000)
            dialogo = page.locator("[role='dialog'], [role='alertdialog']").last
            if await dialogo.count():
                conferma = dialogo.locator("button:has-text('Ritira'), button:has-text('Withdraw'), button[data-test-dialog-primary-btn]").last
                if await conferma.count() == 0:
                    conferma = dialogo.locator("button").last
                await conferma.click()
                await page.wait_for_timeout(1000)
            html = await page.content()
            ancora = S.pulsante_ritira(BeautifulSoup(html, "html.parser"), url) is not None
            return {"esito": "non_trovato" if ancora else "ok"}
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
            page = extractor._page
            # Il DOM non espone il permalink dei post delle persone: lo si legge
            # dalle risposte di rete (campo postSlugUrl), come fa extract_feed.
            permalink: list[str] = []
            attese: list[Any] = []

            def _on_response(resp):
                async def _leggi():
                    try:
                        corpo = (await resp.body()).decode("utf-8", errors="replace")
                    except Exception:
                        return
                    for m in _POST_SLUG_URL_RE.finditer(corpo):
                        u = f"https://www.linkedin.com/posts/{m.group('slug')}"
                        if u not in permalink:
                            permalink.append(u)
                attese.append(asyncio.create_task(_leggi()))

            page.on("response", _on_response)
            try:
                html = await _apri(extractor, url)
                for m in _POST_SLUG_URL_RE.finditer(html):
                    u = f"https://www.linkedin.com/posts/{m.group('slug')}"
                    if u not in permalink:
                        permalink.append(u)
                posts, visti = [], set()
                for _ in range(12):
                    for p in S.parse_feed_posts(html):
                        if p["chiave"] not in visti:
                            visti.add(p["chiave"])
                            posts.append(p)
                    if len(posts) >= limit:
                        break
                    await _scorri(extractor, 2)
                    html = await page.content()
            finally:
                page.remove_listener("response", _on_response)
                if attese:
                    await asyncio.gather(*attese, return_exceptions=True)
            posts = S.unisci_permalink(posts, permalink)
            for p in posts:
                p.pop("chiave", None)
            return posts[:limit]
        except AuthenticationError as e:
            return [_errore_sessione(e)]
        except Exception as e:
            logger.exception("get_feed_posts")
            return [{"errore_lettura": str(e)[:300]}]
