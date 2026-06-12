"""Scoring and tailoring via the Claude API.

The LLM call sits behind an injectable seam (``LLMClient``) so tests can run the
scoring/tailoring logic with a fake client and zero network. The real client
(``AnthropicClient``) reads ANTHROPIC_API_KEY from the environment — the key is a
server-side secret and never reaches a client/frontend.

Security invariant: the untrusted vacancy description is placed ONLY in the user
message, never concatenated into the system instructions. The model has no tools
and no data access beyond what is passed, so prompt-injection blast radius stays
near zero. Keep it this way.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Protocol

try:
    import requests
except ImportError:
    sys.exit("Нет requests. Установи: pip install requests python-docx")

import os

from .models import Job, PlatformConfig, PreScore, MatchResult

ENDPOINT = "https://api.anthropic.com/v1/messages"

# Платформенная константа: дешёвый предфильтр (Haiku).
SCORE_SYSTEM = (
    "Ты быстро оцениваешь соответствие кандидата вакансии. Кандидат работает по B2B "
    "через ИП в Армении (контракт, без EOR). Штатный найм в конкретной стране/исключение "
    "контракторов -> снижай fit. Кандидат ТЕХНИЧЕСКИЙ (support/integration + PM): если "
    "вакансия — чисто нетехническая поддержка (телефон/чат/колл-центр, продажи, ресепшн) "
    "без troubleshooting, SQL/API, интеграций, скриптов или работы с системами — это НЕ "
    "его профиль, ставь fit низко. Отвечай ТОЛЬКО JSON: "
    '{"fit_score": <0-100>, "b2b_eligible": "yes|maybe|no", "reason": "<1 строка по-русски>"}'
)

# Платформенная константа: правила честности для полного тюнинга (Sonnet).
SYSTEM = (
    "Ты — ATS-эксперт и ассистент по поиску работы. На входе: полный мастер-CV кандидата "
    "и описание вакансии. Оцени соответствие и подготовь материалы для отклика.\n"
    "Кандидат работает по B2B через sole proprietor (IE) в Армении: инвойсы, EOR не нужен. "
    "Если вакансия требует штатного найма в конкретной стране/штате или исключает контракторов "
    "— снижай fit и ставь b2b_eligible no/maybe.\n"
    "\n"
    "ЖЁСТКИЕ ПРАВИЛА ЧЕСТНОСТИ:\n"
    "- У кандидата ДВА реальных трека: (1) Technical Support & Integration Engineer и "
    "(2) реальный опыт Project Manager — 2 доведённых проекта, команды 6-8, работа со "
    "стейкхолдерами и кросс-функциональная координация. Под саппорт/интеграционные вакансии "
    "веди от поддержки; под project/product management — от PM-опыта (управление, доставка, "
    "координация, стейкхолдеры). Под PM-роли ТАКЖЕ подсвечивай PM-смежные элементы саппорт-"
    "опыта в управленческих формулировках: кросс-функциональная координация с разработчиками "
    "и продакт-менеджерами; внедрённое им командное улучшение процесса с эффектом ~50%; "
    "приоритизация конкурирующих задач под SLA; коммуникация со стейкхолдерами и обучение; "
    "владение регулярной отчётностью и автоматизацией. НО ЧЕСТНО: не приписывай формальное "
    "руководство проектами в саппорт-роли и не выдумывай PM-артефакты (бюджеты, roadmap, "
    "discovery, продуктовые метрики), которых не было — недостающее идёт в ats_missing/gaps.\n"
    "- Кандидат — ТЕХНИЧЕСКИЙ специалист. Если вакансия по сути нетехническая (чистый "
    "телефон/чат/колл-центр, ресепшн, продажи) без troubleshooting, SQL/API, интеграций, "
    "скриптов или работы с системами — ставь fit низко и отметь это в reason/gaps; не "
    "натягивай его технический профиль на такую роль.\n"
    "- Используй ТОЛЬКО факты и навыки из мастер-CV. Ничего не выдумывай: ни метрик, ни "
    "технологий, ни опыта. Если вакансия хочет то, чего у кандидата нет — это идёт в "
    "ats_missing и gaps, НИКОГДА в summary/skills/письмо.\n"
    "- tailored_skills: переупорядочь и переформулируй РЕАЛЬНЫЕ навыки так, чтобы наверх "
    "вышли ключевые слова вакансии, которые у кандидата правда есть. Не добавляй новых.\n"
    "- tailored_summary: 3-4 строки под акцент вакансии, с ключевыми словами вакансии, без "
    "стаффинга, только правдивое.\n"
    "- cover_letter: никогда не пиши, что кандидат не подходит. Продавай. Первый абзац — "
    "привязка к компании/миссии. Про ИП ровно одно предложение по шаблону: 'I operate as an "
    "independent contractor through my registered sole proprietorship (IE) in Armenia and am "
    "fully set up for international B2B engagement.' Никакой кириллицы в английском тексте.\n"
    "- Язык summary/skills/cover_letter — язык вакансии (по умолчанию английский).\n"
    "- Формат cover_letter адаптивно: формальная/корпоративная вакансия -> развёрнутое письмо "
    "~250-320 слов с блоком 'Key strengths:' (4-5 буллетов, маппинг навыка на требование, с "
    "инструментами) и подписью имя+телефон+email; стартап/краткое объявление -> короткое до "
    "~150 слов в 3 абзаца, подпись именем. Сомневаешься — короткое.\n"
    "- cover_letter — ЧИСТЫЙ ТЕКСТ без markdown: никаких '**', '#', '`'. Его вставляют в "
    "письмо/форму как есть. Буллеты в формальном варианте начинай символом '• ' (без жирного).\n"
    "\n"
    "Поля reason, gaps, recruiter_verdict — ПО-РУССКИ (это для кандидата). reason начни с "
    "[формальное] или [короткое].\n"
    "\n"
    "Отвечай СТРОГО валидным JSON без markdown. Схема:\n"
    "{"
    '"fit_score": <int 0-100>, '
    '"b2b_eligible": "yes|maybe|no", '
    '"reason": "<1 строка по-русски>", '
    '"jd_keywords": ["<до 15 ключевых слов/требований вакансии>"], '
    '"ats_present": ["<ключевые слова вакансии, которые у кандидата ЕСТЬ>"], '
    '"ats_missing": ["<требуемое, чего у кандидата НЕТ>"], '
    '"tailored_summary": "<текст>", '
    '"tailored_skills": ["<Категория: навыки>", "..."], '
    '"gaps": "<пробелы: что переформулировать, что реально доучить — по-русски>", '
    '"recruiter_verdict": "<shortlist|maybe|reject + почему + быстрые правки, по-русски>", '
    '"cover_letter": "<текст>"'
    "}"
)


# ---------------------------------------------------------------------------
# LLM seam
# ---------------------------------------------------------------------------
class LLMClient(Protocol):
    """Anything that can turn a (model, system, messages) request into text.
    Real impl posts to Anthropic; tests inject a fake."""
    def complete(self, *, model: str, system, messages: list, max_tokens: int) -> str: ...


class AnthropicClient:
    """Real LLM client. Reads the API key from the environment — a server-side
    secret that never leaves the backend and is never logged."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("Нет ANTHROPIC_API_KEY. Задай: export ANTHROPIC_API_KEY=sk-ant-...")

    def __repr__(self) -> str:  # never leak the key in logs/repr
        return "AnthropicClient(api_key=***)"

    def complete(self, *, model: str, system, messages: list, max_tokens: int) -> str:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        r = requests.post(ENDPOINT, headers=headers, json=body, timeout=90)
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text, strict=False)   # strict=False — терпим control-символы в строках
    except json.JSONDecodeError:
        start = text.find("{")
        if start == -1:
            raise
        obj, _ = json.JSONDecoder(strict=False).raw_decode(text[start:])
        return obj


# ---------------------------------------------------------------------------
# Core scoring / tailoring (LLM client injected, CV/profile passed in)
# ---------------------------------------------------------------------------
def score_fit(job: Job, short_profile: str, config: PlatformConfig, client: LLMClient) -> PreScore:
    """Дешёвый предфильтр на Haiku: только fit_score + b2b + причина. Экономит дорогие вызовы.
    short_profile приходит аргументом (в продукте — выводится из CV конкретного юзера)."""
    user = (
        f"КАНДИДАТ: {short_profile}\n\n"
        f"ВАКАНСИЯ: {job.title} @ {job.company} ({job.location})\n"
        f"{(job.description or '')[:2500]}"
    )
    text = client.complete(
        model=config.model_score, system=SCORE_SYSTEM,
        messages=[{"role": "user", "content": user}], max_tokens=200,
    )
    return PreScore.from_dict(parse_json(text))


def analyze(job: Job, cv_text: str, config: PlatformConfig, client: LLMClient) -> MatchResult:
    """Дорогой шаг на Sonnet: полный тюнинг. system+CV вынесены в кешируемые блоки.
    Полный CV приходит аргументом (cv_text) — функция сама с диска ничего не читает."""
    user_msg = (
        f"ВАКАНСИЯ:\nНазвание: {job.title}\nКомпания: {job.company}\n"
        f"Локация: {job.location}\nСсылка: {job.url}\n\n"
        f"ОПИСАНИЕ ВАКАНСИИ (используй для ATS-ключевых слов и требований):\n"
        f"{(job.description or '')[:6000]}\n\n"
        "Верни JSON по схеме."
    )
    # system как массив блоков; CV помечен cache_control — статичный префикс кешируется
    # между вакансиями (если суммарно >= минимума токенов модели; иначе просто игнорится).
    system_blocks = [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": "МАСТЕР-CV КАНДИДАТА:\n" + cv_text,
         "cache_control": {"type": "ephemeral"}},
    ]
    text = client.complete(
        model=config.model_tailor, system=system_blocks,
        messages=[{"role": "user", "content": user_msg}], max_tokens=4000,
    )
    return MatchResult.from_dict(parse_json(text))
