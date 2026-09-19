"""Нормализация и проверка телефона: `app/services/contacts.py`.

Номера в анкете и в «Изменить контакты» лежат в международном виде с кодом
страны. Российский ввод через 8 и без кода по-прежнему приводится к +7.
"""
import pytest

from app.services.contacts import normalize_phone, validate_contacts


@pytest.mark.parametrize(
    "raw",
    [
        "+79991234567",
        "79991234567",
        "9991234567",
        "8 (999) 123-45-67",
        "  +7 (999) 123-45-67  ",
    ],
)
def test_normalize_phone_privodit_k_kanonu(raw):
    assert normalize_phone(raw) == "+79991234567"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_normalize_phone_pustoe_ostaetsya_pustym(raw):
    assert normalize_phone(raw) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+375 29 123-45-67", "+375291234567"),
        ("+1 (202) 555-0100", "+12025550100"),
        ("00 44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone_prinimaet_mezhdunarodnyy_format(raw, expected):
    assert normalize_phone(raw) == expected
    assert validate_contacts(expected, "+79991234567", "student") == []


@pytest.mark.parametrize("raw", ["12345", "телефон", "+12+345678"])
def test_normalize_phone_ne_lomaet_nevalidnyy_vvod(raw):
    normalized = normalize_phone(raw)
    assert normalized == raw.strip()
    assert validate_contacts(normalized, "+79991234567", "student")


def test_validate_contacts_prinimaet_kanon():
    assert validate_contacts("+79991234567", "+79997654321", "student") == []


def test_validate_contacts_pustoy_nomer_prosit_zapolnit():
    errors = validate_contacts("", "+79997654321", "student")
    assert errors == ["Введи номер телефона"]


def test_validate_contacts_nepolnyy_nomer_daet_oshibku_formata():
    errors = validate_contacts("+12345", "+12345", "student")
    assert len(errors) == 2
    assert all("кодом страны" in e or "код страны" in e for e in errors)
