import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Definir los mensajes y las URLs con sus parámetros
mensajes = [
    {
        "url": "https://api.telegram.org/bot7363494099:AAGE71kbOBjfS8xJCV5RsQ99xPH6gCbj094/sendMessage",
        "params": {"chat_id": "-4255689578", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7305458266:AAEGUn4foa5GoHUgMJoLG98_rg-2jvW1VvI/sendMessage",
        "params": {"chat_id": "980272927", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7170146373:AAERdTVvzHPYUYmIL9hLRdI2yrf7xK1byf4/sendMessage",
        "params": {"chat_id": "925039105", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7305458266:AAEGUn4foa5GoHUgMJoLG98_rg-2jvW1VvI/sendMessage",
        "params": {"chat_id": "980272927", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7305458266:AAEGUn4foa5GoHUgMJoLG98_rg-2jvW1VvI/sendMessage",
        "params": {"chat_id": "980272927", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7388310739:AAFwfRK5vEEV6SEsHB84Sv0OJjHiATPDa20/sendMessage",
        "params": {"chat_id": "5040501527", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7170146373:AAERdTVvzHPYUYmIL9hLRdI2yrf7xK1byf4/sendMessage",
        "params": {"chat_id": "925039105", "text": "Somos los patrones de Bogotá"}
    },
    {
        "url": "https://api.telegram.org/bot7203596673:AAHpT5PTJDzPE1ZEtSJfZT27vK_ePJBx0cs/sendMessage",
        "params": {"chat_id": "-5571915164", "text": "Somos los patrones de Bogotá"}
    }
]

def enviar_mensaje(mensaje):
    """Función para enviar un mensaje HTTP a Telegram."""
    try:
        response = requests.post(mensaje["url"], params=mensaje["params"])
        if response.status_code != 200:
            print(f"Error al enviar mensaje a {mensaje['params']['chat_id']}: {response.status_code}")
        
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar mensaje a {mensaje['params']['chat_id']}: {e}")

# Ejecutar las solicitudes en bucle con hilos
while True:
    with ThreadPoolExecutor(max_workers=8) as executor:  # Crear un pool de hilos con 8 workers
        futures = [executor.submit(enviar_mensaje, mensaje) for mensaje in mensajes]

        for future in as_completed(futures):
            future.result()  # Espera a que todos los hilos terminen
