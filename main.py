import docker
import time
import threading
from notifier import send_alert_email

# --- CONFIGURACIÓN DE RATE LIMITING ---
MAX_RESTARTS = 3          # Máximo de reinicios permitidos
TIME_WINDOW_SEC = 300     # Ventana de tiempo (5 minutos)
# --------------------------------------

# Diccionario para rastrear muertes: { "container_name": [timestamp1, timestamp2...] }
crash_tracker = {}

def is_monitored(container):
    """ Comprueba si el contenedor tiene la etiqueta sentinel.enable=true """
    try:
        return container.labels.get("sentinel.enable", "false").lower() == "true"
    except Exception:
        return False

def check_crash_loop(container_name):
    """
    Registra una nueva caída y devuelve True si el contenedor está en un Crash Loop.
    """
    now = time.time()
    
    if container_name not in crash_tracker:
        crash_tracker[container_name] = []
        
    # Añadir caída actual
    crash_tracker[container_name].append(now)
    
    # Limpiar caídas antiguas fuera de la ventana de tiempo
    crash_tracker[container_name] = [t for t in crash_tracker[container_name] if now - t <= TIME_WINDOW_SEC]
    
    # Si hay demasiadas caídas recientes, estamos en Crash Loop
    is_loop = len(crash_tracker[container_name]) > MAX_RESTARTS
    
    # Eliminar el contenedor del rastreador si ya no tiene caídas para evitar fugas de memoria
    if not crash_tracker[container_name]:
        del crash_tracker[container_name]
        
    return is_loop

def async_send_email(container_name, exit_code, is_critical):
    """ Lanza el envío de correo en un hilo paralelo para no bloquear Sentinel """
    thread = threading.Thread(
        target=send_alert_email, 
        args=(container_name, exit_code, is_critical)
    )
    thread.daemon = True
    thread.start()

def restart_container(client, container_id, container_name, exit_code):
    """ Intenta reiniciar el contenedor y lanza la notificación """
    if check_crash_loop(container_name):
        print(f"🛑 [SENTINEL] CRASH LOOP DETECTADO en {container_name}. Deteniendo Auto-Healer.")
        async_send_email(container_name, exit_code, is_critical=True)
        return

    try:
        print(f"🔄 [SENTINEL] Intentando reiniciar {container_name}...")
        container = client.containers.get(container_id)
        container.restart()
        print(f"✅ [SENTINEL] {container_name} reiniciado con éxito.")
        
        async_send_email(container_name, exit_code, is_critical=False)
    except Exception as e:
        print(f"❌ [SENTINEL] Falló el reinicio de {container_name}: {e}")

def start_monitoring():
    print("🛡️  [SENTINEL] Iniciando vigilancia de contenedores (Protección Activa)...")
    
    while True:
        try:
            client = docker.from_env()
            # Comprobar conexión real (hace un ping al socket)
            client.ping()
            print("🔗 [SENTINEL] Conexión estable con el demonio de Docker.")
            
            filters = {"type": "container", "event": "die"}
            
            for event in client.events(filters=filters, decode=True):
                container_id = event.get("id") or event.get("Actor", {}).get("ID")
                container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "Unknown")
                exit_code = event.get("Actor", {}).get("Attributes", {}).get("exitCode", "Unknown")

                if not container_id:
                    continue

                try:
                    container = client.containers.get(container_id)
                except docker.errors.NotFound:
                    continue 

                if is_monitored(container):
                    print(f"🚨 [SENTINEL] Alerta: {container_name} ha muerto (Exit Code: {exit_code}).")
                    time.sleep(1) # Pequeño buffer
                    restart_container(client, container_id, container_name, exit_code)

        except docker.errors.APIError as e:
            print(f"⚠️  [SENTINEL] Pérdida de conexión con Docker: {e}")
            print("⏳ [SENTINEL] Reintentando conexión en 10 segundos...")
            time.sleep(10)
        except KeyboardInterrupt:
            print("\n🛑 [SENTINEL] Apagando centinela...")
            break
        except Exception as e:
            print(f"❌ [SENTINEL] Error fatal inesperado: {e}")
            time.sleep(10)

if __name__ == "__main__":
    start_monitoring()
