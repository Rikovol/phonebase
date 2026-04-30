"""Lead capture endpoint — receives form submissions from Mobless Studio site
and forwards them to Telegram via the @borisclaudebot.

Uses TELEGRAM_BOT_TOKEN from env (same bot used for phonebase ops chat).
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

router = APIRouter()

# Anton's personal chat — leads go directly to him
MOBLESS_CHAT_ID = "993678231"

PROJECT_TYPES = {
    "website": "Веб-сайт",
    "mobile": "Мобильное приложение",
    "design": "Дизайн",
    "other": "Другое",
}
BUDGETS = {
    "100k": "до 100 000 ₽",
    "100-500k": "100 000 – 500 000 ₽",
    "500k-1m": "500 000 – 1 000 000 ₽",
    "1m+": "от 1 000 000 ₽",
}


class MoblessLead(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    project_type: str | None = Field(default=None, max_length=50)
    budget: str | None = Field(default=None, max_length=50)
    message: str | None = Field(default=None, max_length=4000)
    # Honeypot — real users leave this empty; bots fill all fields
    website: str | None = Field(default=None, max_length=200)

    @field_validator("name", "email", "phone", "message", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


# In-memory rate limiter — survives only as long as the worker process
_RATE: dict[str, list[float]] = {}
_RATE_WINDOW = 60.0   # seconds
_RATE_LIMIT = 5       # max submissions per window per IP


def _rate_limited(ip: str) -> bool:
    now = time.time()
    bucket = _RATE.setdefault(ip, [])
    bucket[:] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(bucket) >= _RATE_LIMIT:
        return True
    bucket.append(now)
    return False


def _escape_md(text: str) -> str:
    """Telegram MarkdownV2 has many reserved chars; we use the legacy 'Markdown'
    parse_mode which only needs minimal escaping. Just neutralize backticks/asterisks
    that would break formatting."""
    return text.replace("`", "'").replace("*", "·").replace("_", " ")


def _format_message(lead: MoblessLead, ip: str) -> str:
    pt = PROJECT_TYPES.get(lead.project_type or "", lead.project_type or "—")
    bg = BUDGETS.get(lead.budget or "", lead.budget or "—")
    lines = [
        "🆕 *Заявка с сайта Mobless*",
        "",
        f"👤 *Имя:* {_escape_md(lead.name)}",
        f"✉️ *Email:* `{_escape_md(lead.email)}`",
    ]
    if lead.phone:
        lines.append(f"📞 *Телефон:* `{_escape_md(lead.phone)}`")
    lines.append(f"🎯 *Тип проекта:* {pt}")
    lines.append(f"💰 *Бюджет:* {bg}")
    if lead.message:
        lines.append("")
        lines.append("💬 *Сообщение:*")
        lines.append(_escape_md(lead.message))
    lines.append("")
    lines.append(f"🌐 IP: `{ip}`")
    return "\n".join(lines)


@router.post("/mobless")
async def submit_mobless_lead(payload: MoblessLead, request: Request):
    """Public endpoint — accepts a lead from the Mobless contact form,
    posts it to Telegram. No auth (public form), but honeypot + rate limit."""
    # Honeypot: bots fill the hidden 'website' field. Silently accept and discard.
    if payload.website:
        return {"ok": True}

    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="Слишком много заявок, попробуйте позже")

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise HTTPException(status_code=503, detail="Lead handling not configured")

    text = _format_message(payload, ip)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={
                "chat_id": MOBLESS_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Не удалось доставить заявку")

    return {"ok": True}
