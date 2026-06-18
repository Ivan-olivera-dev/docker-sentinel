FROM python:3.11-alpine

# Evitar que Python almacene bytecode o buffer de salida (mejor para logs en Docker)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY . .

# Iniciar el vigilante
CMD ["python", "main.py"]
