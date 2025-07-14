# ▶ Base leve
FROM python:3.11-slim

# ▶ Define diretório de trabalho
WORKDIR /app

# ▶ Copia arquivos
COPY requirements.txt ./
COPY app ./app

# ▶ Instala dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

# ▶ Porta (Railway injeta $PORT)
ENV PORT=8000

# ▶ Comando de inicialização
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

