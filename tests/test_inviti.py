import datetime as dt
import pathlib

from linkedin_mcp_server.scraping import inviti_selettori as S

FIX = pathlib.Path(__file__).parent / "fixtures"
OGGI = dt.date(2026, 9, 7)


def test_parse_inviti():
    voci = S.parse_inviti((FIX / "inviti_inviati.html").read_text(), OGGI)
    assert voci[0] == {"url": "linkedin.com/in/mario-rossi-1", "nome": "Mario Rossi", "data": "2026-09-04"}
    assert voci[1] == {"url": "linkedin.com/in/anna-bianchi", "nome": "Anna Bianchi", "data": "2026-08-24"}
    assert voci[2] == {"url": "linkedin.com/in/luigi-verdi", "nome": "Luigi Verdi", "data": "2026-08-20"}
    assert len(voci) == 3


def test_parse_collegamenti():
    voci = S.parse_collegamenti((FIX / "collegamenti.html").read_text(), OGGI)
    assert [v["url"] for v in voci] == ["linkedin.com/in/anna-bianchi", "linkedin.com/in/paolo-neri", "linkedin.com/in/gina-blu"]
    assert [v["data"] for v in voci] == ["2026-09-04", "2026-08-30", "2026-09-05"]


def test_parse_feed_posts():
    posts = S.parse_feed_posts((FIX / "feed.html").read_text())
    assert len(posts) == 2
    assert posts[0]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:7100/"
    assert posts[0]["autore_url"] == "linkedin.com/in/mario-rossi-1" and posts[0]["autore_nome"] == "Mario Rossi"
    assert posts[0]["autore_headline"] == "Titolare @ Rossi Stampi Srl" and "Audi" in posts[0]["testo"]
    assert posts[1]["url"] == "https://www.linkedin.com/posts/coach-due_mindset-activity-7101" and posts[1]["autore_url"] == "linkedin.com/in/coach-due"


def test_pulsante_ritira():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup((FIX / "inviti_inviati.html").read_text(), "html.parser")
    assert S.pulsante_ritira(soup, "linkedin.com/in/anna-bianchi").get_text() == "Withdraw"
    assert S.pulsante_ritira(soup, "linkedin.com/in/nessuno") is None


def test_data_relativa():
    assert S.parse_data_relativa("Inviato ieri", OGGI) == "2026-09-06"
    assert S.parse_data_relativa("Sent 1 month ago", OGGI) == "2026-08-08"
    assert S.parse_data_relativa("4 set 2026", OGGI) == "2026-09-04"
    assert S.parse_data_relativa("", OGGI) is None
