"""Selectors for the sent-invitations, connections and feed pages.

Everything that touches LinkedIn markup for the Bizstudio tools lives here, so
when LinkedIn changes the DOM only this module and its HTML fixtures move.
The parsers are pure functions over ``page.content()`` and are tested against
saved HTML in ``tests/fixtures``.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

URL_INVITI = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"
URL_COLLEGAMENTI = "https://www.linkedin.com/mynetwork/invite-connect/connections/"
URL_FEED = "https://www.linkedin.com/feed/"
URL_RICERCA_POST = "https://www.linkedin.com/search/results/content/?keywords={q}&sortBy=%22date_posted%22"

_RE_PROFILO = re.compile(r"linkedin\.com/in/([^/?#]+)|^/in/([^/?#]+)", re.I)
_RE_NUMERO = re.compile(r"(\d+)")

_MESI = {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
    "jan": 1, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "dec": 12,
}


def slug_profilo(href: str) -> str | None:
    m = _RE_PROFILO.search(unquote(href or ""))
    if not m:
        return None
    return f"linkedin.com/in/{(m.group(1) or m.group(2)).lower()}"


def parse_data_relativa(testo: str, oggi: dt.date) -> str | None:
    """'Inviato 3 giorni fa', 'Sent 2 weeks ago', 'Connesso il 4 set 2026', 'Connected on Sep 4, 2026'."""
    t = (testo or "").strip().lower()
    if not t:
        return None
    m = re.search(r"(\d{1,2})\s+([a-z]{3})[a-z]*\.?\s+(\d{4})", t)
    if m and m.group(2)[:3] in _MESI:
        return dt.date(int(m.group(3)), _MESI[m.group(2)[:3]], int(m.group(1))).isoformat()
    m = re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", t)
    if m and m.group(1)[:3] in _MESI:
        return dt.date(int(m.group(3)), _MESI[m.group(1)[:3]], int(m.group(2))).isoformat()
    n = _RE_NUMERO.search(t)
    quanti = int(n.group(1)) if n else 1
    if any(k in t for k in ("minut", "ora", "ore", "hour", "oggi", "today", "adesso", "now")):
        return oggi.isoformat()
    if any(k in t for k in ("ieri", "yesterday")):
        return (oggi - dt.timedelta(days=1)).isoformat()
    if any(k in t for k in ("giorn", "day")):
        return (oggi - dt.timedelta(days=quanti)).isoformat()
    if any(k in t for k in ("settiman", "week")):
        return (oggi - dt.timedelta(weeks=quanti)).isoformat()
    if any(k in t for k in ("mes", "month")):
        return (oggi - dt.timedelta(days=30 * quanti)).isoformat()
    if any(k in t for k in ("ann", "year")):
        return (oggi - dt.timedelta(days=365 * quanti)).isoformat()
    return None


def _nome_da(card) -> str:
    for sel in (".artdeco-entity-lockup__title", "[data-view-name*='name']", "span[aria-hidden='true']", "strong", "a[href*='/in/']"):
        el = card.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(" ", strip=True)
    return card.get_text(" ", strip=True)[:80]


def _data_da(card, oggi: dt.date) -> str | None:
    t = card.find("time")
    if t is not None:
        if t.get("datetime"):
            return t["datetime"][:10]
        d = parse_data_relativa(t.get_text(" ", strip=True), oggi)
        if d:
            return d
    for el in card.select(".time-badge, .artdeco-entity-lockup__caption, [class*='time'], [class*='caption'], span"):
        d = parse_data_relativa(el.get_text(" ", strip=True), oggi)
        if d:
            return d
    return parse_data_relativa(card.get_text(" ", strip=True), oggi)


def _card_persone(soup, radice_sel: list[str]):
    for sel in radice_sel:
        cards = [c for c in soup.select(sel) if c.select_one("a[href*='/in/']")]
        if cards:
            return cards
    visti, cards = set(), []
    for a in soup.select("a[href*='/in/']"):
        card = a
        for _ in range(6):
            if card.parent is None or card.name in ("li", "article"):
                break
            card = card.parent
        if id(card) in visti:
            continue
        visti.add(id(card))
        cards.append(card)
    return cards


def parse_inviti(html: str, oggi: dt.date | None = None) -> list[dict]:
    oggi = oggi or dt.date.today()
    soup = BeautifulSoup(html, "html.parser")
    out, visti = [], set()
    for card in _card_persone(soup, ["li.invitation-card", "[data-view-name='invitation-card']", "main li", "li"]):
        a = card.select_one("a[href*='/in/']")
        url = slug_profilo(a.get("href")) if a else None
        if not url or url in visti:
            continue
        visti.add(url)
        out.append({"url": url, "nome": _nome_da(card), "data": _data_da(card, oggi)})
    return out


def parse_collegamenti(html: str, oggi: dt.date | None = None) -> list[dict]:
    oggi = oggi or dt.date.today()
    soup = BeautifulSoup(html, "html.parser")
    out, visti = [], set()
    for card in _card_persone(soup, ["li.mn-connection-card", "[data-view-name='connections-list'] li", "main li", "li"]):
        a = card.select_one("a[href*='/in/']")
        url = slug_profilo(a.get("href")) if a else None
        if not url or url in visti:
            continue
        visti.add(url)
        out.append({"url": url, "nome": _nome_da(card), "data": _data_da(card, oggi)})
    return out


def parse_feed_posts(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, visti = [], set()
    for a in soup.select("a[href*='/feed/update/'], a[href*='/posts/']"):
        href = a.get("href") or ""
        url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
        url = url.split("?")[0]
        if url in visti:
            continue
        cont = a
        autore = None
        for _ in range(12):
            if cont.parent is None:
                break
            cont = cont.parent
            autore = cont.select_one("a[href*='/in/']")
            if autore is not None and cont.name in ("article", "div", "li"):
                testo_el = cont.select_one("[class*='commentary'], [class*='update-components-text'], [data-test-id*='main-feed-activity-card__commentary'], p")
                if testo_el is not None or cont.name == "article":
                    break
        if autore is None:
            continue
        visti.add(url)
        head = ""
        for el in cont.select("[class*='description'], [class*='headline'], [class*='subline'], [class*='secondary']"):
            t = el.get_text(" ", strip=True)
            if t and t != _nome_da(cont):
                head = t
                break
        testo_el = cont.select_one("[class*='commentary'], [class*='update-components-text'], p")
        out.append({
            "url": url,
            "autore_url": slug_profilo(autore.get("href")),
            "autore_nome": autore.get_text(" ", strip=True)[:120],
            "autore_headline": head[:200],
            "testo": (testo_el.get_text(" ", strip=True) if testo_el else cont.get_text(" ", strip=True))[:1500],
        })
    return [p for p in out if p["autore_url"]]


def pulsante_ritira(soup, url: str):
    """Returns the withdraw button element of the card for ``url`` or None."""
    for card in _card_persone(soup, ["li.invitation-card", "[data-view-name='invitation-card']", "main li", "li"]):
        a = card.select_one("a[href*='/in/']")
        if a and slug_profilo(a.get("href")) == url:
            return card.select_one("button[aria-label*='ithdraw'], button[aria-label*='itira'], button[data-control-name*='withdraw'], button")
    return None
