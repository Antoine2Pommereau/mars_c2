# Contexte de construction : la racine du dépôt (voir docker-compose.yml)
FROM python:3.12-slim
# Relie le paquet du registre au dépôt (nettoyage par GitHub Actions, .github/workflows/nettoyage_registre.yml)
LABEL org.opencontainers.image.source="https://github.com/Antoine2Pommereau/mars_c2"
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mars ./mars
COPY backend/app.py backend/contenu.py backend/regions.py backend/satellites.py backend/travailleurs.py backend/preuves.py ./
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
