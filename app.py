# app.py
import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configuración de la app
app = FastAPI(title="HTMIAO → Telegram")

# Permitir todos los CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Variables de entorno
TELEGRAM_BOT_TOKEN = '5935593600:AAFeONdWGRxbPXOJsOUr1QPgJcUUBilc3q0'
TELEGRAM_CHAT_ID = '908735123'

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Modelo de entrada
class MensajeIn(BaseModel):
    mensaje: str

# Endpoint
@app.post("/htmiao")
async def htmiao_handler(payload: MensajeIn):
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": payload.mensaje
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(TELEGRAM_API_URL, data=data)

    try:
        resp = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Respuesta inválida de Telegram")

    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail={"telegram": resp})

    return {"status": "enviado", "message_id": resp["result"]["message_id"]}


@app.post("/maikel")
async def htmiao_handler(payload: MensajeIn):
    data = {
        "chat_id": "-4809216697",
        "text": payload.mensaje
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post("https://api.telegram.org/bot7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k/sendMessage", data=data)

    try:
        resp = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Respuesta inválida de Telegram")

    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail={"telegram": resp})

    return {"status": "enviado", "message_id": resp["result"]["message_id"]}



@app.post("/bal1")
async def htmiao_handler(payload: MensajeIn):
    data = {
        "chat_id": "-4848385992",
        "text": payload.mensaje
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post("https://api.telegram.org/bot7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k/sendMessage", data=data)

    try:
        resp = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Respuesta inválida de Telegram")

    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail={"telegram": resp})

    return {"status": "enviado", "message_id": resp["result"]["message_id"]}



@app.post("/trl")
async def htmiao_handler(payload: MensajeIn):
    data = {
        "chat_id": "-4845606034",
        "text": payload.mensaje
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post("https://api.telegram.org/bot7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k/sendMessage", data=data)

    try:
        resp = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Respuesta inválida de Telegram")

    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail={"telegram": resp})

    return {"status": "enviado", "message_id": resp["result"]["message_id"]}



