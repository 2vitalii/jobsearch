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

from .models import Assessment, Generation, Job, MatchResult, PlatformConfig, PreScore

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

# ---------------------------------------------------------------------------
# Honesty rules — ONE shared constant, injected into BOTH system prompts.
# Architectural invariant: never duplicated, always present in both assess+generate.
# ---------------------------------------------------------------------------
HONESTY_RULES = (
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
)

# ---------------------------------------------------------------------------
# SYSTEM_ASSESS: assessment-only prompt. Returns fit/b2b/reason/jd_keywords/
# ats_present/ats_missing/gaps/recruiter_verdict — NO tailored_*/cover_letter.
# ---------------------------------------------------------------------------
SYSTEM_ASSESS = (
    "Ты — ATS-эксперт и ассистент по поиску работы. На входе: полный мастер-CV кандидата "
    "и описание вакансии. Оцени соответствие.\n"
    "Кандидат работает по B2B через sole proprietor (IE) в Армении: инвойсы, EOR не нужен. "
    "Если вакансия требует штатного найма в конкретной стране/штате или исключает контракторов "
    "— снижай fit и ставь b2b_eligible no/maybe.\n"
    "\n"
    + HONESTY_RULES
    + "\n"
    "Поля reason, gaps, recruiter_verdict — ПО-РУССКИ (это для кандидата). reason начни с "
    "[формальное] или [короткое].\n"
    "\n"
    "Отвечай СТРОГО валидным JSON без markdown. Схема ТОЛЬКО с assessment-полями:\n"
    "{"
    '"fit_score": <int 0-100>, '
    '"b2b_eligible": "yes|maybe|no", '
    '"reason": "<1 строка по-русски>", '
    '"jd_keywords": ["<до 15 ключевых слов/требований вакансии>"], '
    '"ats_present": ["<ключевые слова вакансии, которые у кандидата ЕСТЬ>"], '
    '"ats_missing": ["<требуемое, чего у кандидата НЕТ>"], '
    '"gaps": "<пробелы: что переформулировать, что реально доучить — по-русски>", '
    '"recruiter_verdict": "<shortlist|maybe|reject + почему + быстрые правки, по-русски>"'
    "}"
)

# ---------------------------------------------------------------------------
# SYSTEM_GENERATE: generation-only prompt. Receives assessment context in the
# user message (ats_present + jd_keywords) so it doesn't re-score.
# Returns ONLY tailored_summary, tailored_skills, cover_letter.
# ---------------------------------------------------------------------------
SYSTEM_GENERATE = (
    "Ты — ATS-эксперт и ассистент по поиску работы. На входе: полный мастер-CV кандидата, "
    "описание вакансии и результаты предварительной оценки (ats_present, jd_keywords). "
    "Подготовь материалы для отклика.\n"
    "Кандидат работает по B2B через sole proprietor (IE) в Армении: инвойсы, EOR не нужен.\n"
    "\n"
    + HONESTY_RULES
    + "\n"
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
    "Отвечай СТРОГО валидным JSON без markdown. Схема ТОЛЬКО с generation-полями:\n"
    "{"
    '"tailored_summary": "<текст>", '
    '"tailored_skills": ["<Категория: навыки>", "..."], '
    '"cover_letter": "<текст>"'
    "}"
)

# ---------------------------------------------------------------------------
# Legacy combined SYSTEM (kept for analyze() backward compat in pipeline/tests)
# ---------------------------------------------------------------------------
SYSTEM = (
    "Ты — ATS-эксперт и ассистент по поиску работы. На входе: полный мастер-CV кандидата "
    "и описание вакансии. Оцени соответствие и подготовь материалы для отклика.\n"
    "Кандидат работает по B2B через sole proprietor (IE) в Армении: инвойсы, EOR не нужен. "
    "Если вакансия требует штатного найма в конкретной стране/штате или исключает контракторов "
    "— снижай fit и ставь b2b_eligible no/maybe.\n"
    "\n"
    + HONESTY_RULES
    + "\n"
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


def assess(job: Job, cv_text: str, config: PlatformConfig, client: LLMClient) -> Assessment:
    """Expensive Sonnet step: assessment only (scoring + ATS analysis, NO generation).
    Returns an Assessment with ONLY the 8 assessment fields — structurally cannot
    leak tailored_summary/tailored_skills/cover_letter (honesty invariant #1).
    cv_text comes in as an argument — the function reads nothing from disk."""
    user_msg = (
        f"ВАКАНСИЯ:\nНазвание: {job.title}\nКомпания: {job.company}\n"
        f"Локация: {job.location}\nСсылка: {job.url}\n\n"
        f"ОПИСАНИЕ ВАКАНСИИ (используй для ATS-ключевых слов и требований):\n"
        f"{(job.description or '')[:6000]}\n\n"
        "Верни JSON по схеме (только assessment-поля)."
    )
    # system as list of blocks; CV marked cache_control — static prefix is cached
    # across vacancies (if token count >= model minimum; otherwise just ignored).
    system_blocks = [
        {"type": "text", "text": SYSTEM_ASSESS},
        {"type": "text", "text": "МАСТЕР-CV КАНДИДАТА:\n" + cv_text,
         "cache_control": {"type": "ephemeral"}},
    ]
    text = client.complete(
        model=config.model_tailor, system=system_blocks,
        messages=[{"role": "user", "content": user_msg}], max_tokens=2000,
    )
    try:
        return Assessment.from_dict(parse_json(text))
    except Exception:
        # Robust fallback: return zeroed Assessment rather than crashing the loop.
        return Assessment(
            fit_score=0, b2b="", reason="parse error", jd_keywords=[],
            ats_present=[], ats_missing=[], gaps="", recruiter_verdict="",
        )


def generate(
    job: Job,
    cv_text: str,
    assessment: Assessment,
    config: PlatformConfig,
    client: LLMClient,
) -> Generation:
    """Expensive Sonnet step: generation only (tailored CV text + cover letter).
    Receives the Assessment to include ats_present + jd_keywords in the user
    message so the model does not re-score — keeps the two concerns separate.
    Returns a Generation with ONLY tailored_summary, tailored_skills, cover_letter."""
    user_msg = (
        f"ВАКАНСИЯ:\nНазвание: {job.title}\nКомпания: {job.company}\n"
        f"Локация: {job.location}\nСсылка: {job.url}\n\n"
        f"ОПИСАНИЕ ВАКАНСИИ:\n"
        f"{(job.description or '')[:6000]}\n\n"
        f"РЕЗУЛЬТАТЫ ОЦЕНКИ (не пересчитывай, используй для таргетинга):\n"
        f"ats_present: {assessment.ats_present}\n"
        f"jd_keywords: {assessment.jd_keywords}\n\n"
        "Верни JSON по схеме (только generation-поля)."
    )
    system_blocks = [
        {"type": "text", "text": SYSTEM_GENERATE},
        {"type": "text", "text": "МАСТЕР-CV КАНДИДАТА:\n" + cv_text,
         "cache_control": {"type": "ephemeral"}},
    ]
    text = client.complete(
        model=config.model_tailor, system=system_blocks,
        messages=[{"role": "user", "content": user_msg}], max_tokens=2500,
    )
    try:
        return Generation.from_dict(parse_json(text))
    except Exception:
        # Robust fallback: return empty Generation rather than crashing.
        return Generation(tailored_summary="", tailored_skills=[], cover_letter="")


def analyze(job: Job, cv_text: str, config: PlatformConfig, client: LLMClient) -> MatchResult:
    """Thin wrapper: assess + generate → MatchResult (for CLI pipeline backward compat).
    Do NOT duplicate prompts — delegates to the split functions above."""
    a = assess(job, cv_text, config, client)
    g = generate(job, cv_text, a, config, client)
    return MatchResult.from_assessment_and_generation(a, g)
