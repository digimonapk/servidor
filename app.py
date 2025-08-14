# app.py
import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional

# Configura FastAPI
app = FastAPI(title="HTMIAO → Telegram")

# Habilitar todos los CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite cualquier origen
    allow_credentials=True,
    allow_methods=["*"],  # Permite cualquier método (GET, POST, etc.)
    allow_headers=["*"],  # Permite cualquier cabecera
)

# Variables de entorno para el bot
TELEGRAM_BOT_TOKEN = '5935593600:AAFeONdWGRxbPXOJsOUr1QPgJcUUBilc3q0'
TELEGRAM_CHAT_ID = '908735123'

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Modelo de entrada
class MessageIn(BaseModel):
    message: str = Field(..., min_length=1, description="Texto a enviar a Telegram")
    parse_mode: Optional[str] = None
    disable_web_page_preview: Optional[bool] = False
    disable_notification: Optional[bool] = False

# Endpoint
@app.post("/htmiao")
async def htmiao_handler(payload: MessageIn):
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": payload.message,
        "disable_web_page_preview": payload.disable_web_page_preview,
        "disable_notification": payload.disable_notification,
    }
    if payload.parse_mode:
        data["parse_mode"] = payload.parse_mode

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(TELEGRAM_API_URL, data=data)

    try:
        resp = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Respuesta inválida de Telegram")

    if not resp.get("ok"):
        raise HTTPException(
            status_code=502,
            detail={"error": "Telegram no aceptó el mensaje", "telegram": resp},
        )

    return {"status": "sent", "message_id": resp["result"]["message_id"]}
