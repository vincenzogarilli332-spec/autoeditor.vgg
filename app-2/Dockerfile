FROM python:3.11-slim

# ffmpeg per il montaggio + font DejaVu per il testo sovrimpresso
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# Le clip e i video generati vengono salvati qui: su Railway/Render,
# collega un Volume persistente su questo percorso per non perdere
# tutto ad ogni nuovo deploy.
RUN mkdir -p /app/backend/storage

EXPOSE 8000

CMD ["sh", "-c", "cd backend && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
