# 24/7 бот в облаке / на VPS (когда домашний ПК выключен)
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# код (секреты — через env / volume, не в image)
COPY *.py ./
COPY media ./media
# config.example — шаблон; реальный config монтируют или BOT_TOKEN в env
COPY config.example.json ./config.example.json

# state/media должны быть writable
RUN mkdir -p /app/media /data && \
    if [ ! -f /app/config.json ]; then cp /app/config.example.json /app/config.json; fi

# volume для persistent state (опционально -v /data:/app)
VOLUME ["/app/media", "/app"]

CMD ["python", "bot.py"]
