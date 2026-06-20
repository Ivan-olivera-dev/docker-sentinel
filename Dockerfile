FROM python:3.11-alpine

# Evitar que Python almacene bytecode o buffer de salida (mejor para logs en Docker)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Crear usuario sin privilegios
RUN addgroup -S sentinelgroup && adduser -S sentineluser -G sentinelgroup

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY . .

# Cambiar la propiedad de los archivos al nuevo usuario
RUN chown -R sentineluser:sentinelgroup /app

# Cambiar a usuario no-root
USER sentineluser

# Iniciar el vigilante
CMD ["python", "main.py"]
