"""Создать/обновить гостевую ссылку на пробник и билеты — временный модуль (Трек B).

Читает JSON-файл с конфигом и билетами, upsert-ит GuestExamConfig по token,
заливает картинки билетов в S3 (если указан локальный путь) и печатает ссылку.

Формат JSON:
{
  "token": "trial-26-28-aug",
  "title": "Пробный экзамен Apparchi",
  "starts_at": "2026-08-26 00:00",
  "ends_at": "2026-08-28 23:59",
  "tickets": [
    {"subject": "Рисунок", "title": "Натюрморт", "description": "...", "image": "C:/path/img.jpg"},
    {"subject": "Композиция", "title": "..."}
  ]
}

Повторный запуск с тем же token обновляет даты/заголовок конфига и ДОБАВЛЯЕТ билеты
(старые не трогает и не удаляет) — для замены набора билетов деактивировать старые
руками в БД (is_active=False) или очистить перед повторным запуском.

Запуск:
    python scripts/create_guest_exam_config.py path/to/config.json
"""
import argparse
import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from app.config import settings
    from app.db.database import SessionLocal
    from app.models.guest_exam import GuestExamConfig, GuestTicket
    from app.services import s3 as s3_service
    from app.services.tz import MSK_TZ
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Project dependencies are missing. Install them first, for example: pip install -r requirements.txt"
    ) from exc


def _parse_msk(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=MSK_TZ)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update guest exam config + tickets")
    parser.add_argument("json_path", help="Путь к JSON-файлу с конфигом и билетами")
    args = parser.parse_args()

    with open(args.json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        config = db.query(GuestExamConfig).filter(GuestExamConfig.token == data["token"]).first()
        if config:
            config.title = data["title"]
            config.starts_at = _parse_msk(data["starts_at"])
            config.ends_at = _parse_msk(data["ends_at"])
            config.is_active = True
        else:
            config = GuestExamConfig(
                token=data["token"],
                title=data["title"],
                starts_at=_parse_msk(data["starts_at"]),
                ends_at=_parse_msk(data["ends_at"]),
                is_active=True,
            )
            db.add(config)
        db.flush()

        added = 0
        for ticket_data in data.get("tickets", []):
            image_url = None
            image_path = None
            local_image = ticket_data.get("image")
            if local_image and os.path.isfile(local_image):
                with open(local_image, "rb") as img_f:
                    raw = img_f.read()
                content_type = mimetypes.guess_type(local_image)[0] or "image/jpeg"
                image_path = (
                    f"guest-exam/tickets/{config.token}/{ticket_data['subject']}/"
                    f"{uuid.uuid4().hex[:8]}_{os.path.basename(local_image)}"
                )
                image_url = s3_service.upload_to_s3(image_path, raw, content_type=content_type)

            db.add(GuestTicket(
                config_id=config.id,
                subject=ticket_data["subject"],
                title=ticket_data["title"],
                description=ticket_data.get("description"),
                image_s3_url=image_url,
                image_s3_path=image_path,
                is_active=True,
            ))
            added += 1

        db.commit()
        print(f"Ссылка: https://{settings.domain}/guest/{config.token}")
        print(f"Окно: {config.starts_at} — {config.ends_at} (МСК)")
        print(f"Билетов добавлено в этом запуске: {added}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
