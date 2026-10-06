# Image des scripts AIS en direct : collecte AISStream, ingestion en base, import des listes de surveillance.
# Contexte de construction : la racine du dépôt (voir docker-compose.yml).
FROM python:3.12-slim
WORKDIR /app
COPY scripts/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mars ./mars
COPY scripts ./scripts
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
CMD ["python", "scripts/ais_ingest.py"]
