"""Render contracts for the final interface audit."""


def _login_as(client, session_factory, user):
    session = session_factory(user)
    client.cookies.set("session_id", session.id)


def test_public_login_uses_one_primary_heading_and_touchable_staff_link(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert "<h2>Войдите в личный кабинет</h2>" not in response.text
    assert "min-height: 44px" in response.text


def test_superadmin_role_select_names_the_affected_user(
    client,
    user_factory,
    session_factory,
):
    superadmin = user_factory(
        vk_id=401001,
        name="Phase Four Admin",
        role_name="суперадмин",
    )
    user_factory(
        vk_id=401002,
        name="Phase Four Curator",
        role_name="куратор",
    )
    _login_as(client, session_factory, superadmin)

    response = client.get("/cabinet/superadmin/users")

    assert response.status_code == 200
    assert 'aria-label="Роль пользователя Phase Four Curator"' in response.text


def test_3dlab_opacity_slider_has_an_accessible_name(
    client,
    user_factory,
    session_factory,
):
    student = user_factory(vk_id=401003, role_name="ученик")
    _login_as(client, session_factory, student)

    response = client.get("/3dlab")

    assert response.status_code == 200
    assert 'aria-label="Прозрачность вставки"' in response.text
    assert '<img id="schemeImage" src=""' not in response.text
    assert 'image.id = "schemeImage"' in response.text


def test_closed_upload_flows_do_not_initialize_missing_form_controls(
    client,
    user_factory,
    session_factory,
):
    student = user_factory(vk_id=401004, role_name="ученик")
    _login_as(client, session_factory, student)

    mock_response = client.get("/upload/mock-exam")
    retake_response = client.get("/upload/retake")

    assert mock_response.status_code == 200
    assert retake_response.status_code == 200
    assert "finalInput.addEventListener" not in mock_response.text
    assert "input.addEventListener('change'" not in retake_response.text
