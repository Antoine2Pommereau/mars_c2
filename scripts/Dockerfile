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
# Lot Z1 : versions figées avec leurs empreintes (scripts/requirements.lock, produit par uv depuis requirements.txt ;
# docs/exploitation.md) : une même empreinte de commit installe toujours les mêmes paquets
COPY scripts/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
# Après les dépendances : changer de commit ne refait pas l'installation
# Empreinte du commit (docker build --build-arg MARS_VERSION=…) : resource service.version des traces, signes de vie,
# indicateur « Version » de la barre d'état et tableau Grafana
ARG MARS_VERSION=dev
ENV MARS_VERSION=$MARS_VERSION
LABEL org.opencontainers.image.revision=$MARS_VERSION
COPY mars ./mars
COPY scripts ./scripts
COPY travailleurs ./travailleurs
# Migrations : tables attendues d'une sauvegarde lors de l'essai de restauration (mars/restauration.py)
COPY db/init ./db/init
# Requêtes des tableaux Grafana, pour taches.py mesurer-tableaux (durées sur la base du serveur)
COPY grafana/generer.py ./grafana/generer.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
CMD ["python", "scripts/ais_ingest.py"]
