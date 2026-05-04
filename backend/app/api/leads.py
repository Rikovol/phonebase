"""Lead capture endpoint — receives form submissions from Mobless Studio site
and forwards them to Telegram via the @borisclaudebot.

Uses TELEGRAM_BOT_TOKEN from env (same bot used for phonebase ops chat).
"""
from __future__ import annotations

import html

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.limiter import limiter

router = APIRouter()

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


def _format_message(lead: MoblessLead, ip: str) -> str:
    # Telegram HTML parse_mode: безопасно для URL, скобок, кавычек.
    # Экранируем пользовательский ввод через html.escape, статичный текст — нет.
    e = html.escape
    pt = PROJECT_TYPES.get(lead.project_type or "", lead.project_type or "—")
    bg = BUDGETS.get(lead.budget or "", lead.budget or "—")
    lines = [
        "🆕 <b>Заявка с сайта Mobless</b>",
        "",
        f"👤 <b>Имя:</b> {e(lead.name)}",
        f"✉️ <b>Email:</b> <code>{e(lead.email)}</code>",
    ]
    if lead.phone:
        lines.append(f"📞 <b>Телефон:</b> <code>{e(lead.phone)}</code>")
    lines.append(f"🎯 <b>Тип проекта:</b> {e(pt)}")
    lines.append(f"💰 <b>Бюджет:</b> {e(bg)}")
    if lead.message:
        lines.append("")
        lines.append("💬 <b>Сообщение:</b>")
        lines.append(e(lead.message))
    lines.append("")
    lines.append(f"🌐 IP: <code>{e(ip)}</code>")
    return "\n".join(lines)


@router.post("/mobless")
@limiter.limit("5/minute")
async def submit_mobless_lead(request: Request, payload: MoblessLead):
    """Public endpoint — accepts a lead from the Mobless contact form,
    posts it to Telegram. No auth (public form), but honeypot + rate limit
    (Redis-backed через slowapi)."""
    # Honeypot: bots fill the hidden 'website' field. Silently accept and discard.
    if payload.website:
        return {"ok": True}

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise HTTPException(status_code=503, detail="Lead handling not configured")

    ip = request.client.host if request.client else "unknown"
    text = _format_message(payload, ip)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={
                "chat_id": settings.LEADS_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Не удалось доставить заявку")

    return {"ok": True}
