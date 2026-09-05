import datetime as dt
import pathlib

from bs4 import BeautifulSoup

from linkedin_mcp_server.scraping import inviti_selettori as S

FIX = pathlib.Path(__file__).parent / "fixtures"
OGGI = dt.date(2026, 9, 7)


def test_parse_inviti():
    voci = S.parse_inviti((FIX / "inviti_inviati.html").read_text(), OGGI)
    assert voci[0] == {"url": "linkedin.com/in/mario-rossi-1", "nome": "Mario Rossi", "data": "2026-09-05"}
    assert voci[1] == {"url": "linkedin.com/in/anna-bianchi", "nome": "Anna Bianchi", "data": "2026-09-05"}
    assert voci[2] == {"url": "linkedin.com/in/luigi-verdi", "nome": "Luigi Verdi", "data": "2026-08-31"}
    assert len(voci) == 3


def test_parse_collegamenti():
    voci = S.parse_collegamenti((FIX / "collegamenti.html").read_text(), OGGI)
    assert [v["url"] for v in voci] == ["linkedin.com/in/anna-bianchi", "linkedin.com/in/paolo-neri", "linkedin.com/in/gina-blu"]
    assert [v["nome"] for v in voci] == ["Anna Bianchi", "Paolo Neri", "Gina Blu"]
    assert [v["data"] for v in voci] == ["2026-09-04", "2026-08-30", "2026-09-05"]


def test_parse_feed_posts():
    posts = S.parse_feed_posts((FIX / "feed.html").read_text())
    assert [p["autore_url"] for p in posts] == ["linkedin.com/in/mario-rossi-1", "linkedin.com/in/luigi-verdi", "linkedin.com/in/gina-blu"]
    assert posts[0]["autore_nome"] == "Mario Rossi" and posts[0]["autore_headline"] == "Titolare @ Rossi Stampi Srl"
    assert "Audi" in posts[0]["testo"] and posts[0]["url"] is None
    assert posts[1]["autore_headline"] == "Mindset coach · 10x il tuo business" and "mindset" in posts[1]["testo"].lower()
    assert posts[2]["autore_nome"] == "Gina Blu" and posts[2]["autore_headline"] == "CEO Blu Plastics"
    assert len({p["chiave"] for p in posts}) == 3


def test_unisci_permalink():
    posts = [{"url": None, "autore_url": "linkedin.com/in/mario-rossi-1"}, {"url": None, "autore_url": "linkedin.com/in/gina-blu"},
             {"url": "https://www.linkedin.com/feed/update/urn:li:share:1/", "autore_url": "linkedin.com/in/luigi-verdi"}]
    link = ["https://www.linkedin.com/posts/gina-blu_packaging-activity-7104-abcd?utm=1",
            "https://www.linkedin.com/posts/mario-rossi-1_stampi-activity-7100-zzzz",
            "https://www.linkedin.com/posts/altro_x-activity-1-y"]
    out = S.unisci_permalink(posts, link)
    assert out[0]["url"] == "https://www.linkedin.com/posts/mario-rossi-1_stampi-activity-7100-zzzz"
    assert out[1]["url"] == "https://www.linkedin.com/posts/gina-blu_packaging-activity-7104-abcd"
    assert out[2]["url"].endswith("share:1/")


def test_pulsante_ritira():
    soup = BeautifulSoup((FIX / "inviti_inviati.html").read_text(), "html.parser")
    el = S.pulsante_ritira(soup, "linkedin.com/in/anna-bianchi")
    assert el is not None and el.name == "a" and "Anna Bianchi" in el.get("aria-label", "")
    assert S.pulsante_ritira(soup, "linkedin.com/in/nessuno") is None


def test_data_relativa():
    assert S.parse_data_relativa("Inviato ieri", OGGI) == "2026-09-06"
    assert S.parse_data_relativa("Sent 1 month ago", OGGI) == "2026-08-08"
    assert S.parse_data_relativa("4 set 2026", OGGI) == "2026-09-04"
    assert S.parse_data_relativa(" 5 settembre 2026", OGGI) == "2026-09-05"
    assert S.parse_data_relativa("", OGGI) is None


def _post(chiave, testa, testo="testo del post lungo abbastanza"):
    return f"<div componentkey='{chiave}'><h2>Post nel feed</h2><div>{testa}</div><p><span>{testo}</span></p></div>"


def test_post_commentato_prende_l_autore_vero_e_salta_le_aziende():
    k1, k2, k3 = "a" * 43, "b" * 43, "c" * 43
    html = "<main>" + _post(k1, "<a href='https://www.linkedin.com/in/chi-commenta/'>Chi Commenta</a><span>Chi Commenta ha aggiunto un commento</span>"
                            "<a href='https://www.linkedin.com/in/autore-vero/'><span>Autore Vero</span><span>• 2°</span></a><span>Titolare @ Vero Srl</span><span>3g •</span>") \
        + _post(k2, "<a href='https://www.linkedin.com/in/chi-consiglia/'>Chi Consiglia</a><span>consiglia questo</span>"
                    "<a href='https://www.linkedin.com/company/acme/'><span>Acme Spa</span></a><span>1.000 follower</span>") \
        + _post(k3, "<a href='https://www.linkedin.com/company/acme/'><span>Acme Spa</span></a>") + "</main>"
    posts = S.parse_feed_posts(html)
    assert [p["autore_url"] for p in posts] == ["linkedin.com/in/autore-vero"]
    assert posts[0]["autore_nome"] == "Autore Vero" and posts[0]["autore_headline"] == "Titolare @ Vero Srl"
