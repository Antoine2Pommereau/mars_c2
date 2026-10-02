# Contexte de construction : la racine du dépôt (voir docker-compose.yml)
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mars ./mars
COPY backend/app.py backend/rules.py ./
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
