import json
import os
import httpx

KIE_API_BASE = "https://api.kie.ai"
KIE_IMAGE_MODEL = os.getenv("KIE_IMAGE_MODEL", "gpt-image-2-text-to-image")
KIE_REPORT_ASPECT_RATIO = os.getenv("KIE_REPORT_ASPECT_RATIO", "2:3")


def _rub(value) -> str:
    return f"{float(value or 0):,.0f}".replace(",", " ") + " ₽"


def _month_title(year: int, month: int) -> str:
    names = {
        1: "январь", 2: "февраль", 3: "март", 4: "апрель",
        5: "май", 6: "июнь", 7: "июль", 8: "август",
        9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
    }
    return names.get(month, str(month)) + " " + str(year)


async def build_report_prompt(user_id: int, year: int, month: int) -> str:
    from app.database import fetchall, get_category_breakdown, get_monthly_summary

    summary = await get_monthly_summary(user_id, year, month)
    categories = await get_category_breakdown(user_id, year, month)
    planned_rows = await fetchall(
        """SELECT name, amount, amount_is_approximate, next_trigger_date
           FROM recurring_payments
           WHERE user_id=%s
             AND is_active=TRUE
             AND type='expense'
             AND EXTRACT(YEAR FROM next_trigger_date)=%s
             AND EXTRACT(MONTH FROM next_trigger_date)=%s
           ORDER BY next_trigger_date, name
           LIMIT 8""",
        (user_id, year, month),
    )
    notes = await fetchall(
        "SELECT text FROM notes WHERE user_id=%s ORDER BY created_at DESC LIMIT 5",
        (user_id,),
    )

    top_categories = categories[:6]
    category_lines = [
        f"- {row['name']}: {_rub(row['total'])}" for row in top_categories
    ] or ["- пока мало данных"]
    planned_lines = [
        f"- {row[3].strftime('%d.%m')} {row[0]}: {'~' if row[2] else ''}{_rub(row[1])}"
        for row in (planned_rows or [])
    ] or ["- нет планируемых платежей в этом месяце"]
    note_lines = ["- " + str(row[0])[:120] for row in (notes or [])] or ["- нет заметок"]

    return (
        "Сделай вертикальный красивый инфографический финансовый отчёт для Telegram на русском языке. "
        "Формат: чистый современный fintech-дизайн, светлый фон, акцентные зелёные и графитовые элементы, "
        "крупные цифры, аккуратные карточки без перегруза, понятная иерархия. "
        "Не добавляй вымышленные цифры. Текст должен быть читаемым.\n\n"
        f"Заголовок: Баланс · {_month_title(year, month)}\n"
        f"Доходы: {_rub(summary['income'])}\n"
        f"Расходы: {_rub(summary['total_expense'])}\n"
        f"Баланс месяца: {_rub(summary['balance'])}\n"
        f"Остаток с учётом прошлого периода: {_rub(summary['closing_balance'])}\n\n"
        "Топ расходов:\n" + "\n".join(category_lines) + "\n\n"
        "Планируемые платежи:\n" + "\n".join(planned_lines) + "\n\n"
        "Заметки пользователя для контекста:\n" + "\n".join(note_lines) + "\n\n"
        "Добавь короткий блок 'Вывод' на 1-2 строки: спокойный, полезный, без морализаторства."
    )


async def create_kie_image_task(prompt: str) -> str:
    api_key = os.getenv("KIE_API_KEY")
    if not api_key:
        raise RuntimeError("KIE_API_KEY не настроен")

    callback_url = os.getenv("KIE_CALLBACK_URL")
    payload = {
        "model": KIE_IMAGE_MODEL,
        "input": {
            "prompt": prompt,
            "aspect_ratio": KIE_REPORT_ASPECT_RATIO,
        },
    }
    if callback_url:
        payload["callBackUrl"] = callback_url

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            KIE_API_BASE + "/api/v1/jobs/createTask",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    if data.get("code") != 200:
        raise RuntimeError(data.get("msg") or "Kie.ai не принял задачу")
    task_id = (data.get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError("Kie.ai не вернул taskId")
    return task_id


async def get_kie_task(task_id: str) -> dict:
    api_key = os.getenv("KIE_API_KEY")
    if not api_key:
        raise RuntimeError("KIE_API_KEY не настроен")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            KIE_API_BASE + "/api/v1/jobs/recordInfo",
            headers={"Authorization": "Bearer " + api_key},
            params={"taskId": task_id},
        )
        response.raise_for_status()
        data = response.json()

    if data.get("code") not in (200, 505):
        raise RuntimeError(data.get("msg") or "Не удалось получить статус Kie.ai")
    return data.get("data") or {}


def extract_result_url(task_data: dict) -> str | None:
    result_json = task_data.get("resultJson")
    if not result_json:
        return None
    try:
        result = json.loads(result_json)
    except (TypeError, json.JSONDecodeError):
        return None
    for key in ("resultUrls", "urls", "images"):
        value = result.get(key)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url") or first.get("imageUrl")
    return result.get("url") or result.get("imageUrl")


async def start_beautiful_report(user_id: int, year: int, month: int) -> dict:
    prompt = await build_report_prompt(user_id, year, month)
    task_id = await create_kie_image_task(prompt)
    return {"task_id": task_id, "prompt": prompt}
