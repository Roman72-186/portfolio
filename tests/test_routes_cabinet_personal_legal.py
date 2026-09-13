def test_personal_page_has_legal_buttons(db, client, session_factory, user_factory):
    student = user_factory()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert "openLegalDoc('oferta'" in resp.text
    assert 'id="legal-modal"' in resp.text


def test_legal_doc_fragment_ok(db, client, session_factory, user_factory):
    student = user_factory()
    client.cookies.set("session_id", session_factory(student).id)

    for slug in ("oferta", "soglasie-pdn", "politika-pdn", "soglasie-rassylka"):
        resp = client.get(f"/cabinet/personal/legal/{slug}")
        assert resp.status_code == 200, slug
        assert "<p>" in resp.text or "<h" in resp.text


def test_legal_doc_unknown_slug_404(db, client, session_factory, user_factory):
    student = user_factory()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal/legal/does-not-exist")
    assert resp.status_code == 404


def test_legal_doc_requires_login(client):
    resp = client.get("/cabinet/personal/legal/oferta", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)
