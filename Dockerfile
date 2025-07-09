# ▶ Base leve
FROM python:3.11-slim

# ▶ Instala dependências
RUN pip install --no-cache fastapi uvicorn pandas openpyxl

# ▶ Copia o app
WORKDIR /app
COPY app ./app

# ▶ Porta; Railway injeta $PORT em runtime
ENV PORT=8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
