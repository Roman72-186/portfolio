"""`scripts/payment_windows.py apply`: окна по плану из номеров — пробный
прогон ничего не пишет, запись не трогает цену и набор, чужое окно — отказ."""
import importlib.util
import io
import json
from argparse import Namespace
from pathlib import Path

import pytest

from app.models.user import User

_SPEC = importlib.util.spec_from_file_location(
    "payment_windows", Path(__file__).resolve().parents[1] / "scripts" / "payment_windows.py",
)
pw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pw)


@pytest.fixture()
def students(db, user_factory):
    a = user_factory(vk_id=980_001, name="Первая", tariff="Я С ВАМИ")
    b = user_factory(vk_id=980_002, name="Вторая", tariff="Я САМ")
    a.pay_price_kop, b.pay_price_kop = 1275500, 677500
    db.commit()
    return a, b


def _run(monkeypatch, plan, apply):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(plan)))
    return pw.cmd_apply(Namespace(apply=apply))


def test_dry_run_writes_nothing(db, monkeypatch, students, capsys):
    a, _ = students
    code = _run(monkeypatch, [{"id": a.id, "window": [25, 30], "paid_through": "2026-09"}], apply=False)
    assert code == 0
    assert "пробный прогон" in capsys.readouterr().out
    db.expire_all()
    assert db.get(User, a.id).pay_window_start is None


def test_apply_sets_window_and_paid_month_keeps_price(db, monkeypatch, students):
    a, b = students
    plan = [{"id": a.id, "window": [25, 30], "paid_through": "2026-09"},
            {"id": b.id, "window": [10, 15], "paid_through": None}]
    assert _run(monkeypatch, plan, apply=True) == 0
    db.expire_all()
    a, b = db.get(User, a.id), db.get(User, b.id)
    assert (a.pay_window_start, a.pay_window_end, a.pay_price_kop) == (25, 30, 1275500)
    assert a.paid_until is not None  # сентябрь оплачен — закроется после окна октября
    assert (b.pay_window_start, b.pay_window_end, b.pay_price_kop, b.paid_until) == (10, 15, 677500, None)


def test_other_window_refuses_whole_plan(db, monkeypatch, students, capsys):
    a, b = students
    a.pay_window_start, a.pay_window_end = 10, 15
    db.commit()
    plan = [{"id": a.id, "window": [25, 30], "paid_through": None},
            {"id": b.id, "window": [10, 15], "paid_through": None}]
    assert _run(monkeypatch, plan, apply=True) == 1
    assert "уже окно 10–15" in capsys.readouterr().out
    db.expire_all()
    assert db.get(User, b.id).pay_window_start is None  # при отказе не пишется никто
