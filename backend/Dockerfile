# Contexte de construction : la racine du dépôt (voir docker-compose.yml)
FROM python:3.12-slim
# Relie le paquet du registre au dépôt (nettoyage par GitHub Actions, .github/workflows/nettoyage_registre.yml)
LABEL org.opencontainers.image.source="https://github.com/Antoine2Pommereau/mars_c2"
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Après les dépendances : changer de commit ne refait pas l'installation
# Empreinte du commit (docker build --build-arg MARS_VERSION=…) : resource service.version des traces, signes de vie,
# indicateur « Version » de la barre d'état et tableau Grafana
ARG MARS_VERSION=dev
ENV MARS_VERSION=$MARS_VERSION
LABEL org.opencontainers.image.revision=$MARS_VERSION
COPY mars ./mars
COPY backend/app.py backend/contenu.py backend/regions.py backend/satellites.py backend/travailleurs.py backend/preuves.py backend/sentinel.py ./
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
