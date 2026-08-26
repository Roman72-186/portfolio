"""Выгрузить результаты гостевого пробника в CSV — перед сносом временного модуля.

Запуск:
    python scripts/export_guest_exam_results.py --token trial-26-28-aug --out results.csv
"""
import argparse
import csv
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from app.db.database import SessionLocal
    from app.models.guest_exam import GuestExamConfig, GuestParticipant, GuestSubmission
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Project dependencies are missing. Install them first, for example: pip install -r requirements.txt"
    ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Export guest exam results to CSV")
    parser.add_argument("--token", required=True, help="Токен гостевой ссылки (GuestExamConfig.token)")
    parser.add_argument("--out", default="guest_exam_results.csv", help="Путь к CSV-файлу")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        config = db.query(GuestExamConfig).filter(GuestExamConfig.token == args.token).first()
        if not config:
            raise SystemExit(f"Конфиг с token={args.token!r} не найден")

        rows = (
            db.query(GuestSubmission, GuestParticipant)
            .join(GuestParticipant, GuestSubmission.participant_id == GuestParticipant.id)
            .filter(GuestParticipant.config_id == config.id)
            .order_by(GuestSubmission.subject, GuestParticipant.display_name)
            .all()
        )

        with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Имя", "Telegram", "Код участника", "Предмет", "Билет", "Статус",
                "Отправлено", "Балл", "Комментарий", "Фото",
            ])
            for submission, participant in rows:
                writer.writerow([
                    participant.display_name,
                    f"@{participant.telegram_username}" if participant.telegram_username else "",
                    participant.participant_code,
                    submission.subject,
                    submission.ticket_title,
                    submission.status,
                    submission.submitted_at.isoformat() if submission.submitted_at else "",
                    submission.score if submission.score is not None else "",
                    submission.comment or "",
                    submission.s3_url or "",
                ])

        print(f"Выгружено строк: {len(rows)} → {args.out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
