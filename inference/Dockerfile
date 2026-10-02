# Service d'inférence en conteneur, sur CPU (ou GPU NVIDIA avec l'image PyTorch adaptée).
# Sur un Mac, préférer le lancement natif pour bénéficier du GPU Apple : voir README.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml .
COPY mars ./mars
COPY inference ./inference
RUN pip install --no-cache-dir . fastapi "uvicorn[standard]"
CMD ["uvicorn", "inference.app:app", "--host", "0.0.0.0", "--port", "8001"]
