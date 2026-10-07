# Image des scripts AIS et d'exploitation : collecte AISStream, ingestion en base, import des listes de surveillance,
# tâches planifiées (archivage R2, purge, sauvegarde, disque). Contexte : la racine du dépôt (docker-compose.yml).
FROM python:3.12-slim
# Relie le paquet du registre au dépôt (nettoyage par GitHub Actions, .github/workflows/nettoyage_registre.yml)
LABEL org.opencontainers.image.source="https://github.com/Antoine2Pommereau/mars_c2"
# pg_dump doit être de la même version majeure que le serveur (PostgreSQL 16) : dépôt officiel PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY scripts/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mars ./mars
COPY scripts ./scripts
COPY travailleurs ./travailleurs
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
CMD ["python", "scripts/ais_ingest.py"]
