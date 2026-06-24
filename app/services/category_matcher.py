import difflib
import json
import os
import re

import httpx

from app.database import get_categories, fetchone


def normalize_name(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str) -> list[str]:
    return [token for token in normalize_name(value).split() if len(token) > 2]


async def _fallback_category(user_id: int, type_hint: str | None) -> dict | None:
    tx_type = type_hint if type_hint in ("expense", "income") else "expense"
    categories = await get_categories(user_id, type_=tx_type)
    if not categories:
        return None

    default_name = "прочие расходы" if tx_type == "expense" else "прочие доходы"
    for cat in categories:
        if default_name in normalize_name(cat["name"]):
            return {
                "category_id": cat["id"],
                "category_name": cat["name"],
                "type": cat["type"],
                "kind": cat["kind"],
                "confidence": 0.35,
                "reason": "Категория по умолчанию",
            }

    cat = categories[0]
    return {
        "category_id": cat["id"],
        "category_name": cat["name"],
        "type": cat["type"],
        "kind": cat["kind"],
        "confidence": 0.3,
        "reason": "Первая доступная категория",
    }


async def _ask_openai(user_id: int, text: str, categories: list[dict], type_hint: str | None) -> dict | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not categories:
        return None

    categories_payload = [
        {
            "id": cat["id"],
            "name": cat["name"],
            "type": cat["type"],
            "kind": cat["kind"],
        }
        for cat in categories
        if not type_hint or cat["type"] == type_hint
    ]
    if not categories_payload:
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "response_format": {"type": "json_object"},
                    "max_tokens": 180,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Выбери лучшую категорию для финансовой операции. "
                                "Верни JSON: category_id, confidence, reason. "
                                "Выбирай только из переданного списка."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "text": text,
                                    "type_hint": type_hint,
                                    "categories": categories_payload,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
                timeout=10.0,
            )
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        category_id = int(parsed.get("category_id"))
        confidence = float(parsed.get("confidence", 0))
    except Exception:
        return None

    allowed = {cat["id"]: cat for cat in categories_payload}
    if category_id not in allowed or confidence < 0.55:
        return None

    row = await fetchone(
        "SELECT id FROM categories WHERE id=%s AND user_id=%s",
        (category_id, user_id),
    )
    if not row:
        return None

    cat = allowed[category_id]
    return {
        "category_id": cat["id"],
        "category_name": cat["name"],
        "type": cat["type"],
        "kind": cat["kind"],
        "confidence": confidence,
        "reason": parsed.get("reason") or "Подобрано ИИ",
    }


async def match_category(
    user_id: int,
    text: str,
    type_hint: str | None = None,
    amount: float | None = None,
    source: str = "text",
) -> dict | None:
    categories = await get_categories(user_id, type_=type_hint if type_hint in ("expense", "income") else None)
    if not categories:
        return None

    text_norm = normalize_name(text)
    text_tokens = set(_tokens(text))

    best = None
    best_score = 0.0
    best_reason = ""

    for cat in categories:
        name_norm = normalize_name(cat["name"])
        name_tokens = set(_tokens(cat["name"]))
        if not name_norm:
            continue

        if name_norm in text_norm:
            best = cat
            best_score = 0.98
            best_reason = "Название категории прямо указано в тексте"
            break

        overlap = text_tokens & name_tokens
        if overlap:
            score = min(0.9, 0.65 + 0.1 * len(overlap))
            if score > best_score:
                best = cat
                best_score = score
                best_reason = "Совпали слова из названия категории"

        for token in text_tokens:
            for name_token in name_tokens:
                ratio = difflib.SequenceMatcher(None, token, name_token).ratio()
                if ratio > best_score:
                    best = cat
                    best_score = ratio
                    best_reason = "Похоже на название категории"

        ratio = difflib.SequenceMatcher(None, text_norm, name_norm).ratio()
        if ratio > best_score:
            best = cat
            best_score = ratio
            best_reason = "Похожая фраза"

    if best and best_score >= 0.62:
        return {
            "category_id": best["id"],
            "category_name": best["name"],
            "type": best["type"],
            "kind": best["kind"],
            "confidence": round(best_score, 2),
            "reason": best_reason,
        }

    ai_match = await _ask_openai(user_id, text, categories, type_hint)
    if ai_match:
        return ai_match

    return await _fallback_category(user_id, type_hint)
