"""Окна оплаты ученикам из списка школы — запуск оплаты через сайт.

Зачем отдельно от «Оплаты списком» (`services/payment_import.py`). Та
загрузка ищет ученика по имени и фамилии в Excel школы и ставит суммы. Суммы
уже стоят (12.10.2026, `docs/ops-history.md`), а в списке школы есть номер
ученика на платформе (колонка `ID`). Осталось окно и «оплачено по» — по
номеру надёжнее, чем по имени.

Два шага.

1. `plan` — дома, без базы. Из CSV школы (`oplata/Ученики_оплата.csv`, вне
   git: ФИО и ники) и списка оплативших октябрь собирает план — только номера,
   окна и месяц «оплачено по»:

       python scripts/payment_windows.py plan --csv ../oplata/Ученики_оплата.csv \\
           --group 25-30 --paid-through 2026-09 > ../oplata/plan-25-30.json
       python scripts/payment_windows.py plan --csv ../oplata/Ученики_оплата.csv \\
           --group 10-15 --paid-through 2026-10 --paid-ids ../oplata/oplatili-oktyabr.txt \\
           --unpaid-through 2026-09 > ../oplata/plan-10-15.json

   «Оплачено по» решает, закроет ли кабинет по неоплате
   (`PAYMENTS_BLOCK_ENABLED`): пусто — не закроет никогда. Группе 25–30
   «сентябрь» — не заплатил в окно 25–30.10, закроется 31.10 в 2:00. Группе
   10–15 — «октябрь» оплатившим по старой ссылке (список школы), остальным —
   `--unpaid-through` (сентябрь — закроется сразу, как включат блокировку;
   не задан — пусто, должник видит «Оплатить», но не закрывается).

2. `apply` — в контейнере прода, план из stdin. По умолчанию пробный прогон
   с откатом; `--apply` пишет. Через `payments.apply_payment_settings`, как
   карточка: журнал, проверки; цена, набор и «тариф после оплаты» ученика не
   меняются. Окно, уже поставленное иначе, не перетирается — строка в отказ.

       ssh apparchi-prod "docker exec -i -w /app -e PYTHONPATH=/app \\
           portfolio-saas-app-1 python scripts/payment_windows.py apply" < plan.json
       … apply --apply   # запись, только по слову владельца

После записи — строка в `docs/ops-history.md`. Отменить: тот же план с
пустыми окнами не годится (пустое окно запрещает «оплачено по»), откат —
карточкой или `apply_payment_settings(window_start=None, …, paid_through=None)`.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path

PERFORMED_BY_ID = 3  # суперадмин-владелец, как в прошлых ручных записях оплаты
NOT_PAYING_IDS = {282}  # бесплатно насовсем — владелец 13.10.2026


def _month(text: str | None) -> str | None:
    if not text:
        return None
    year, month = text.split("-")
    date(int(year), int(month), 1)  # проверка
    return f"{int(year):04d}-{int(month):02d}"


def cmd_plan(args) -> int:
    raw = Path(args.csv).read_bytes().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw), delimiter=";"))
    head = [h.strip() for h in rows[0]]
    col_id, col_window = head.index("ID"), head.index("Дата оплаты")
    start, end = (int(x) for x in args.group.split("-"))
    paid_ids: set[int] = set()
    if args.paid_ids:
        paid_ids = {int(x) for x in Path(args.paid_ids).read_text(encoding="utf-8").split() if x.strip()}

    plan, seen = [], set()
    for row in rows[1:]:
        if len(row) <= max(col_id, col_window) or not row[col_id].strip():
            continue
        uid = int(row[col_id])
        if uid in NOT_PAYING_IDS or row[col_window].strip().replace("–", "-") != args.group:
            continue
        seen.add(uid)
        through = _month(args.paid_through) if (not args.paid_ids or uid in paid_ids) else _month(args.unpaid_through)
        plan.append({"id": uid, "window": [start, end], "paid_through": through})

    unknown = paid_ids - seen
    if unknown:
        print(f"В списке оплативших нет в группе {args.group}: {sorted(unknown)}", file=sys.stderr)
        return 1
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    by = {}
    for p in plan:
        by[p["paid_through"]] = by.get(p["paid_through"], 0) + 1
    print(f"Группа {args.group}: {len(plan)} учеников, «оплачено по»: {by}", file=sys.stderr)
    return 0


def cmd_apply(args) -> int:
    from app.db.database import SessionLocal
    from app.models.user import User
    from app.services import payments

    plan = json.load(sys.stdin)
    db = SessionLocal()
    ok = refused = 0
    try:
        for item in plan:
            uid, (start, end) = item["id"], item["window"]
            through = payments.parse_paid_month(item["paid_through"] or "")
            user = db.get(User, uid)
            why = None
            if user is None:
                why = "нет такого"
            elif not user.is_active:
                why = "неактивен"
            elif not user.pay_price_kop and not payments.reference_price_kop(db, user.tariff, user.pay_cohort):
                why = "нет цены"
            elif payments.has_window(user) and (user.pay_window_start, user.pay_window_end) != (start, end):
                why = f"уже окно {user.pay_window_start}–{user.pay_window_end}"
            if why:
                refused += 1
                print(f"#{uid}: ОТКАЗ — {why}")
                continue
            payments.apply_payment_settings(
                db, PERFORMED_BY_ID, user,
                window_start=start, window_end=end,
                cohort=user.pay_cohort or "", price_kop=user.pay_price_kop,
                paid_through=through,
            )
            ok += 1
            until = payments.msk_text(user.paid_until) if user.paid_until else "—"
            print(f"#{uid}: окно {start}–{end}, оплачено по {item['paid_through'] or '—'}, "
                  f"срок {until}, цена {payments.rub_text(payments.price_kop(db, user))}")
        print(f"Итого: записать {ok}, отказ {refused}")
        if args.apply and not refused:
            db.commit()
            print("ЗАПИСАНО")
        else:
            db.rollback()
            print("пробный прогон — откат" if not args.apply else "есть отказы — ничего не записано")
    finally:
        db.close()
    return 0 if not refused else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--csv", required=True)
    p.add_argument("--group", required=True, choices=["10-15", "25-30"])
    p.add_argument("--paid-through", help="«оплачено по» (ГГГГ-ММ) — всем или оплатившим из --paid-ids")
    p.add_argument("--paid-ids", help="файл с номерами оплативших, через пробел или с новой строки")
    p.add_argument("--unpaid-through", help="«оплачено по» тем, кого нет в --paid-ids; пусто — не задавать")
    a = sub.add_parser("apply")
    a.add_argument("--apply", action="store_true", help="записать; без флага — пробный прогон")
    args = parser.parse_args()
    return cmd_plan(args) if args.cmd == "plan" else cmd_apply(args)


if __name__ == "__main__":
    sys.exit(main())
