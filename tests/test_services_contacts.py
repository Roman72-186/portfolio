"""Нормализация и проверка телефона: `app/services/contacts.py`.

Номера в анкете и в «Изменить контакты» принимаются только российские и лежат
в базе одной строкой `+7XXXXXXXXXX`. До 13.09.2026 поле принимало любую запись
из цифр, пробелов, плюса, минуса и скобок, поэтому один и тот же человек
попадал в базу как «8 999…», «+7 (999) …» и «9991234567» — три разных номера
при поиске и выгрузке.

Отдельно закреплено, что иностранный номер отбивается ошибкой, а не молча
превращается в российский обрезкой лишних цифр.
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
        "+8 999 123 45 67",
        "  +7 (999) 123-45-67  ",
    ],
)
def test_normalize_phone_privodit_k_kanonu(raw):
    assert normalize_phone(raw) == "+79991234567"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_normalize_phone_pustoe_ostaetsya_pustym(raw):
    assert normalize_phone(raw) == ""


@pytest.mark.parametrize("raw", ["+375291234567", "12345", "телефон", "+1 202 555 0100"])
def test_normalize_phone_ne_lomaet_chuzhoy_format(raw):
    """Что не разобрали — возвращаем как есть, решение об ошибке за валидацией.

    Иностранный номер не должен «дообрезаться» до российского: последние десять
    цифр белорусского номера — чужой человек, а не тот, кто заполнял анкету.
    """
    normalized = normalize_phone(raw)
    assert normalized == raw.strip()
    assert validate_contacts(normalized, "+79991234567", "student")


def test_validate_contacts_prinimaet_kanon():
    assert validate_contacts("+79991234567", "+79997654321", "student") == []


def test_validate_contacts_pustoy_nomer_prosit_zapolnit():
    errors = validate_contacts("", "+79997654321", "student")
    assert errors == ["Введи номер телефона"]


def test_validate_contacts_neros_nomer_daet_oshibku_formata():
    errors = validate_contacts("+375291234567", "+375291234567", "student")
    assert len(errors) == 2
    assert all("российский" in e for e in errors)
