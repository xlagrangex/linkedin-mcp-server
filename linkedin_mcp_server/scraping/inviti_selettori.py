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


_RE_CHIAVE_POST = re.compile(r"^[A-Za-z0-9_-]{43}$")
_RE_TEMPO = re.compile(r"^\d+\s*[a-zà-ú]{1,4}\s*•?$")
_RE_DATA = re.compile(r"inviat|sent|data collegamento|connected|collegat", re.I)
_RE_PERMALINK = re.compile(r"linkedin\.com/posts/([^_/?#]+)_", re.I)
_RE_CONTESTO = re.compile(r"ha aggiunto un commento|consiglia|festeggia|ha reagito|ha condiviso|commented|celebrat|likes this|reposted|Consigliato per te|Suggested", re.I)


def _testo(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def _card_persone(soup, radice_sel: list[str]):
    """Le card della pagina: prima i contenitori noti, poi un fallback per link /in/."""
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


def _voce(card, oggi: dt.date) -> dict | None:
    """Una persona da una card (inviti o collegamenti, markup SDUI 2026).

    Nome nel primo <p>, headline nel primo <span> che non è né il nome né una data,
    data nel testo che contiene «Inviato …» o «Data collegamento: …».
    """
    a = card.select_one("a[href*='/in/']")
    url = slug_profilo(a.get("href")) if a else None
    if not url:
        return None
    p = card.find("p")
    nome = _testo(p) if p is not None and not _RE_DATA.search(_testo(p)) else ""
    if not nome:
        nome = next((_testo(x) for x in card.select("a[href*='/in/']") if _testo(x)), "").split("•")[0].strip()
    data = None
    for el in card.find_all(["span", "p", "time"]):
        t = _testo(el)
        if el.name == "time" and el.get("datetime"):
            data = el["datetime"][:10]
            break
        if _RE_DATA.search(t) and len(t) < 60:
            data = parse_data_relativa(t.split(":")[-1], oggi)
            if data:
                break
    headline = next((_testo(sp) for sp in card.find_all("span")
                     if _testo(sp) and _testo(sp) != nome and not _RE_DATA.search(_testo(sp))
                     and _testo(sp) not in ("Ritira", "Withdraw", "Messaggio", "Message") and len(_testo(sp)) > 3), "")
    return {"url": url, "nome": nome[:120], "data": data, "headline": headline[:200]}


def parse_inviti(html: str, oggi: dt.date | None = None) -> list[dict]:
    oggi = oggi or dt.date.today()
    soup = BeautifulSoup(html, "html.parser")
    out, visti = [], set()
    for card in _card_persone(soup, ["div[role='listitem']", "li.invitation-card", "[data-view-name='invitation-card']", "main li"]):
        v = _voce(card, oggi)
        if v and v["url"] not in visti:
            visti.add(v["url"])
            out.append({"url": v["url"], "nome": v["nome"], "data": v["data"]})
    return out


def parse_collegamenti(html: str, oggi: dt.date | None = None) -> list[dict]:
    oggi = oggi or dt.date.today()
    soup = BeautifulSoup(html, "html.parser")
    out, visti = [], set()
    for card in _card_persone(soup, ["div[componentkey^='ConnectionCard_']", "li.mn-connection-card", "[data-view-name='connections-list'] li", "main li"]):
        v = _voce(card, oggi)
        if v and v["url"] not in visti:
            visti.add(v["url"])
            out.append({"url": v["url"], "nome": v["nome"], "data": v["data"]})
    return out


def conta_contenitori(html: str) -> tuple[int, int]:
    """(contenitori a chiave 43, di cui con un commento): per capire cosa c'è nel DOM a ogni scroll."""
    soup = BeautifulSoup(html, "html.parser")
    tutti = _contenitori_post(soup)
    con_testo = [p for p in tutti if p.find("p", recursive=False) is not None or p.find(attrs={"componentkey": re.compile(r"^(expanded|translatable-commentary)")}) is not None]
    return len(tutti), len(con_testo)


def _contenitori_post(soup):
    """I post sono <div componentkey="<43 caratteri>"> non annidati in un altro uguale."""
    tutti = [d for d in soup.find_all(True) if _RE_CHIAVE_POST.match(str(d.get("componentkey", "")))]
    return [d for d in tutti if not any(_RE_CHIAVE_POST.match(str(p.get("componentkey", ""))) for p in d.parents)]


def parse_feed_posts(html: str) -> list[dict]:
    """Un record per post con autore persona: {url, autore_url, autore_nome, autore_headline, testo, chiave}.

    ``url`` è valorizzata solo se il markup la contiene (shareId nel commento
    traducibile); altrimenti resta None e la si completa con
    :func:`unisci_permalink` dai link catturati sulla rete.
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for post in _contenitori_post(soup):
        figli = post.find_all(recursive=False)
        commento = post.find("p", recursive=False) or post.find(attrs={"componentkey": re.compile(r"^(expanded|translatable-commentary)")})
        if commento is None:
            continue  # caroselli «Consigliato per te», annunci, moduli senza testo
        testa = next((f for f in figli if f.find("a", href=re.compile(r"/in/"))), post)
        # Nell'intestazione «X ha aggiunto un commento» il primo link è di chi ha
        # commentato: l'autore è l'ultimo link persona con testo. Se dopo di lui
        # c'è un link azienda con testo, il post è di un'azienda.
        ancore = [a for a in testa.select("a[href*='/in/'], a[href*='/company/']") if _testo(a)]
        persone = [a for a in ancore if "/in/" in a.get("href", "")]
        if not persone:
            continue
        autore = persone[-1]
        if any("/company/" in a.get("href", "") for a in ancore[ancore.index(autore) + 1:]):
            continue
        nome_el = autore.find("span")
        nome = _testo(nome_el) if nome_el is not None else _testo(autore).split("•")[0].strip()
        spans = [_testo(sp) for sp in testa.find_all("span")]
        dopo = spans[spans.index(nome) + 1:] if nome in spans else spans
        headline = next((t for t in dopo if t and not t.startswith("•") and not _RE_TEMPO.match(t)
                         and not _RE_CONTESTO.search(t) and t not in ("Segui", "Follow") and len(t) > 3), "")
        testo = _testo(commento)
        url = None
        chiave = post.find(attrs={"componentkey": re.compile(r"^translatable-commentary")})
        m = re.search(r"shareId=(\d+)", str(chiave.get("componentkey")) if chiave is not None else "")
        if m:
            url = f"https://www.linkedin.com/feed/update/urn:li:share:{m.group(1)}/"
        out.append({
            "url": url,
            "autore_url": slug_profilo(autore.get("href")),
            "autore_nome": nome[:120],
            "autore_headline": headline[:200],
            "testo": testo[:1500],
            "chiave": post.get("componentkey"),
            "contesto": next((t for t in spans if _RE_CONTESTO.search(t)), ""),
        })
    return [p for p in out if p["autore_url"]]


def unisci_permalink(posts: list[dict], permalink: list[str]) -> list[dict]:
    """Assegna a ogni post senza url il primo permalink libero il cui slug autore coincide.

    I permalink hanno forma linkedin.com/posts/<vanity>_<titolo>-activity-<id>-<hash>.
    """
    liberi = list(permalink)
    for p in posts:
        if p.get("url"):
            continue
        vanity = unquote((p["autore_url"] or "").rsplit("/", 1)[-1]).lower()
        for u in liberi:
            m = _RE_PERMALINK.search(unquote(u))
            if m and m.group(1).lower() == vanity:
                p["url"] = u.split("?")[0]
                liberi.remove(u)
                break
    return posts


def pulsante_ritira(soup, url: str):
    """Il controllo «Ritira» della card di ``url``: un <a aria-label="Ritira l’invito…"> nel markup 2026, o un bottone."""
    for card in _card_persone(soup, ["div[role='listitem']", "li.invitation-card", "[data-view-name='invitation-card']", "main li"]):
        a = card.select_one("a[href*='/in/']")
        if a and slug_profilo(a.get("href")) == url:
            return card.select_one("a[aria-label*='itira'], a[aria-label*='ithdraw'], button[aria-label*='ithdraw'], button[aria-label*='itira'], button[data-control-name*='withdraw'], button")
    return None


def autore_da_permalink(url: str) -> str | None:
    """`/posts/<vanity>_...` porta il vanity dell'autore: è il profilo, senza aprire nulla."""
    m = _RE_PERMALINK.search(url or "")
    return f"linkedin.com/in/{unquote(m.group(1))}" if m else None


def _primo_visibile(el) -> str | None:
    """Nel markup classico nome e headline sono ripetuti per gli screen reader:
    la copia buona è lo span aria-hidden."""
    if el is None:
        return None
    span = el.select_one("span[aria-hidden='true']")
    return _testo(span or el) or None


def parse_post_voyager(html: str, url: str) -> dict | None:
    """La pagina di un singolo post (`/posts/<slug>`) usa ancora il markup classico:
    `div.feed-shared-update-v2` con attore e commento nelle classi `update-components-*`."""
    soup = BeautifulSoup(html, "html.parser")
    radice = soup.select_one("div.feed-shared-update-v2")
    if radice is None:
        return None
    attore = radice.select_one("a.update-components-actor__meta-link[href*='/in/']")
    testo = radice.select_one(".update-components-text")
    return {
        "url": url,
        "autore_url": slug_profilo(attore["href"]) if attore else autore_da_permalink(url),
        "autore_nome": _primo_visibile(radice.select_one(".update-components-actor__title")),
        "autore_headline": _primo_visibile(radice.select_one(".update-components-actor__description")),
        "testo": testo.get_text("\n", strip=True) if testo else "",
    }


def parse_post_pagina(html: str, url: str) -> dict:
    """Un singolo post: prima il markup classico della pagina `/posts/`, poi quello
    SDUI del feed, infine i meta tag con l'autore letto dal permalink."""
    classico = parse_post_voyager(html, url)
    if classico:
        return classico
    posts = parse_feed_posts(html)
    if posts:
        p = dict(posts[0], url=url)
        p.pop("chiave", None)
        p.pop("contesto", None)
        if not p.get("autore_url"):
            p["autore_url"] = autore_da_permalink(url)
        return p
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one("meta[property='og:description'], meta[name='description']")
    return {"url": url, "autore_url": autore_da_permalink(url), "autore_nome": None, "autore_headline": None,
            "testo": (meta.get("content") or "").strip() if meta else ""}
