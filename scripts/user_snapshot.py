"""Полное удаление ученика со снимком — и восстановление из снимка.

`hard_delete_user` безвозвратен: до 19.10.2026 откат был только из копии всей
базы, то есть вместе с чужими данными. Здесь перед удалением собираются все
строки, которые оно снесёт (по внешним ключам с каскадом и по таблицам, что
`hard_delete_user` чистит явно), и ссылки, которые оно обнулит. Восстановление
вставляет те же строки с теми же id и возвращает обнулённые ссылки — ученик
возвращается с номером, историей и Telegram, остальная база не откатывается.

Строки берёт и возвращает сама Postgres (`row_to_json` /
`json_populate_recordset`), типы не проходят через Python.

    # пробный прогон: снимок, удаление, проверка, восстановление, проверка — и откат
    docker exec -i -w /app -e PYTHONPATH=/app portfolio-saas-app-1 \
        python - delete --user 277 --by 3 --dry-run < scripts/user_snapshot.py
    # настоящее удаление: снимок пишется в --out внутри контейнера, забрать docker cp
    ... python - delete --user 277 --by 3 --out /tmp/u277.json < scripts/user_snapshot.py
    # восстановление (Telegram ученика не должен быть занят новым профилем)
    ... python - restore --file /tmp/u277.json < scripts/user_snapshot.py

Если после снимка в таблице появилась колонка NOT NULL без значения в снимке —
восстановление упадёт целиком, ничего не записав.
"""
import argparse
import json
import sys

from sqlalchemy import MetaData, text

from app.db.database import SessionLocal
from app.services import user_management as um

# Что `hard_delete_user` удаляет явно, без каскада в базе: (модель, колонка).
EXPLICIT_DELETE = [
    (um.Work, "user_id"), (um.Feedback, "work_id"), (um.ExamCycle, "user_id"),
    (um.Notification, "user_id"), (um.LegacyPortfolioPhoto, "user_id"), (um.UserTag, "user_id"),
    (um.UploadLog, "user_id"), (um.LoginToken, "user_id"), (um.TelegramLinkToken, "user_id"),
    (um.MockExamLock, "user_id"), (um.Session, "user_id"), (um.StudentActivityEvent, "user_id"),
    (um.AuditLog, "target_user_id"), (um.AuditLog, "performed_by_id"),
]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _edges(meta: MetaData):
    """(дочерняя таблица, колонка, родитель, колонка родителя, удаляется ли строка)."""
    explicit = {}
    for model, col in EXPLICIT_DELETE:
        table = model.__table__
        parent = next((fk.column for fk in table.c[col].foreign_keys), None)
        explicit[(table.name, col)] = (parent.table.name, parent.name) if parent is not None else ("users", "id")
    edges = {}
    for table in meta.sorted_tables:
        for fk in table.foreign_key_constraints:
            if len(fk.elements) != 1:
                raise SystemExit(f"составной внешний ключ в {table.name} — скрипт его не умеет")
            el = fk.elements[0]
            key = (table.name, el.parent.name)
            cascade = (fk.ondelete or "").upper() == "CASCADE" or key in explicit
            edges[key] = (el.column.table.name, el.column.name, cascade)
    for key, (ptable, pcol) in explicit.items():
        edges.setdefault(key, (ptable, pcol, True))
    return [(c, col, p, pc, d) for (c, col), (p, pc, d) in edges.items()]


def _pk(meta: MetaData, table: str, row: dict) -> tuple:
    cols = [c.name for c in meta.tables[table].primary_key.columns] or sorted(row)
    return tuple(row[c] for c in cols)


def snapshot(conn, meta: MetaData, user_id: int) -> dict:
    rows: dict[str, dict] = {}
    first = conn.execute(text("SELECT row_to_json(t) FROM users t WHERE id = :i"), {"i": user_id}).scalar()
    if first is None:
        raise SystemExit(f"пользователя {user_id} нет")
    rows["users"] = {_pk(meta, "users", first): first}
    edges = _edges(meta)
    nulls: dict[tuple, dict] = {}
    changed = True
    while changed:
        changed = False
        for child, col, parent, pcol, cascade in edges:
            if parent not in rows:
                continue
            values = sorted({r[pcol] for r in rows[parent].values() if r.get(pcol) is not None})
            if not values:
                continue
            found = conn.execute(
                text(f"SELECT row_to_json(t) FROM {_q(child)} t WHERE {_q(col)} = ANY(:v)"), {"v": values},
            ).scalars().all()
            for row in found:
                key = _pk(meta, child, row)
                if cascade:
                    if key not in rows.setdefault(child, {}):
                        rows[child][key] = row
                        changed = True
                else:
                    nulls[(child, key, col)] = {"table": child, "pk": dict(zip(
                        [c.name for c in meta.tables[child].primary_key.columns], key)),
                        "column": col, "value": row[col]}
    # Обнуление внутри удаляемых строк не в счёт — они вернутся целиком.
    kept = [n for (t, key, _), n in nulls.items() if key not in rows.get(t, {})]
    return {"user_id": user_id, "rows": {t: list(r.values()) for t, r in rows.items() if r}, "nulls": kept}


def restore(conn, meta: MetaData, snap: dict) -> None:
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    for table in meta.sorted_tables:
        data = snap["rows"].get(table.name)
        if data:
            conn.execute(
                text(f"INSERT INTO {_q(table.name)} SELECT * FROM json_populate_recordset(NULL::{_q(table.name)}, :j)"),
                {"j": json.dumps(data)},
            )
    for n in snap["nulls"]:
        where = " AND ".join(f"{_q(c)} = :pk_{i}" for i, c in enumerate(n["pk"]))
        params = {f"pk_{i}": v for i, v in enumerate(n["pk"].values())}
        conn.execute(text(f"UPDATE {_q(n['table'])} SET {_q(n['column'])} = :v WHERE {where} AND {_q(n['column'])} IS NULL"),
                     {**params, "v": n["value"]})
    conn.execute(text("SET LOCAL session_replication_role = origin"))


def counts(conn, meta: MetaData) -> dict:
    return {t.name: conn.execute(text(f"SELECT count(*) FROM {_q(t.name)}")).scalar() for t in meta.sorted_tables}


def _null_left(conn, n: dict):
    where = " AND ".join(f"{_q(c)} = :pk_{i}" for i, c in enumerate(n["pk"]))
    params = {f"pk_{i}": v for i, v in enumerate(n["pk"].values())}
    return conn.execute(text(f"SELECT {_q(n['column'])} FROM {_q(n['table'])} WHERE {where}"), params).scalar()


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("delete")
    d.add_argument("--user", type=int, required=True)
    d.add_argument("--by", type=int, required=True)
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--out")
    r = sub.add_parser("restore")
    r.add_argument("--file", required=True)
    args = ap.parse_args()

    db = SessionLocal()
    conn = db.connection()
    meta = MetaData()
    meta.reflect(bind=conn)

    if args.cmd == "restore":
        snap = json.load(open(args.file, encoding="utf-8"))
        restore(conn, meta, snap)
        db.commit()
        print("восстановлено:", {t: len(v) for t, v in snap["rows"].items()}, "ссылок:", len(snap["nulls"]))
        return 0

    if not args.dry_run and not args.out:
        raise SystemExit("для настоящего удаления нужен --out")
    before = counts(conn, meta)
    snap = snapshot(conn, meta, args.user)
    print("в снимке:", {t: len(v) for t, v in snap["rows"].items()}, "обнуляемых ссылок:", len(snap["nulls"]))

    ok, err = um.hard_delete_user(db, args.user, args.by, commit=False)
    if not ok:
        db.rollback()
        raise SystemExit(f"удаление отказало: {err}")
    db.flush()
    after = counts(conn, meta)
    problems = []
    for table, n in before.items():
        expected = n - len(snap["rows"].get(table, [])) + (1 if table == "audit_logs" else 0)
        if after[table] != expected:
            problems.append(f"{table}: было {n}, стало {after[table]}, ждали {expected}")
    for n in snap["nulls"]:
        if _null_left(conn, n) is not None:
            problems.append(f"не обнулено: {n['table']}.{n['column']} {n['pk']}")
    if problems:
        db.rollback()
        raise SystemExit("снимок не совпал с удалением, откат:\n" + "\n".join(problems))
    print("удаление совпало со снимком")

    if args.dry_run:
        restore(conn, meta, snap)
        restored = counts(conn, meta)
        diff = [t for t in before if restored[t] != before[t] + (1 if t == "audit_logs" else 0)]
        again = snapshot(conn, meta, args.user)
        same = all(
            sorted(map(json.dumps, again["rows"].get(t, []))) == sorted(map(json.dumps, v))
            for t, v in snap["rows"].items()
        ) and all(_null_left(conn, n) == n["value"] for n in snap["nulls"])
        db.rollback()
        print("пробное восстановление:", "строки и ссылки вернулись как были" if not diff and same
              else f"РАСХОЖДЕНИЕ {diff} same={same}")
        print("пробный прогон — всё откачено")
        return 0 if not diff and same else 1

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False)
    db.commit()
    print(f"удалено, снимок — {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
