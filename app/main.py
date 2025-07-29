from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from openpyxl import Workbook, load_workbook
import tempfile
import fitz  # PyMuPDF
import base64
import os
from fpdf import FPDF
import traceback
from collections import defaultdict
import re
import traceback
from datetime import datetime, timedelta
import textwrap
from fpdf import FPDF
import hashlib
import logging
from typing import List

app = FastAPI()

# Caminho da fonte para PDF com acentos
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

@app.post("/xlsx-to-json")
async def convert_xlsx_to_json(file: UploadFile = File(...)):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        workbook = load_workbook(filename=tmp_path, data_only=True)
        all_data = {}

        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            rows = list(sheet.iter_rows(values_only=True))

            if not rows:
                continue

            headers = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
            data_rows = rows[1:]
            data = []

            for row in data_rows:
                row_dict = {headers[i]: row[i] for i in range(len(headers)) if headers[i] != ""}
                data.append(row_dict)

            all_data[sheet_name] = data

        return JSONResponse(content=all_data)

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/split-pdf")
async def split_pdf(file: UploadFile = File(...)):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        doc = fitz.open(tmp_path)
        pages_b64 = []

        for i in range(len(doc)):
            single_page = fitz.open()
            single_page.insert_pdf(doc, from_page=i, to_page=i)

            # Corrigido: salvar com compactação e limpeza de objetos
            page_path = f"/tmp/page_{i+1}.pdf"
            single_page.save(page_path, garbage=4, deflate=True, incremental=False)

            with open(page_path, "rb") as f:
                b64_content = base64.b64encode(f.read()).decode("utf-8")
                pages_b64.append({
                    "page": i + 1,
                    "file_base64": b64_content,
                    "filename": f"page_{i+1}.pdf"
                })

            os.remove(page_path)

        return JSONResponse(content={"pages": pages_b64})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# --- INÍCIO normaliza-escala-from-pdf ---
checkCollision(ball, paddle) {
        // Expanded collision box for better detection
        const ballBox = {
            x: ball.x - ball.radius - 2,
            y: ball.y - ball.radius - 2,
            width: ball.radius * 2 + 4,
            height: ball.radius * 2 + 4
        };
        
        const paddleBox = {
            x: paddle.x - paddle.width / 2 - 2,
            y: paddle.y - paddle.height / 2 - 2,
            width: paddle.width + 4,
            height: paddle.height + 4
        };
        
        if (ballBox.x < paddleBox.x + paddleBox.width &&
            ballBox.x + ballBox.width > paddleBox.x &&
            ballBox.y < paddleBox.y + paddleBox.height &&
            ballBox.y + ballBox.height > paddleBox.y) {
            
            // Prevent double hits
            if (paddle.lastHitFrame && this.frameCount - paddle.lastHitFrame < 10) {
                return false;
            }
            paddle.lastHitFrame = this.frameCount;
            
            // Calculate hit position on paddle (-1 to 1)
            const hitPos = (ball.x - paddle.x) / (paddle.width / 2);
            
            // Enhanced angle calculation with velocity influence
            const baseAngle = hitPos * Math.PI / 3; // -60° to +60°
            const velocityFactor = ball.vx / 15; // Velocity influence
            const angle = baseAngle + velocityFactor * 0.3;
            
            // Speed boost based on hit position
            const speedMultiplier = 1 + Math.abs(hitPos) * 0.2;
            const currentSpeed = Math.sqrt(ball.vx * ball.vx + ball.vy * ball.vy);
            const newSpeed = Math.min(currentSpeed * speedMultiplier, 15);
            
            // Calculate new velocity
            ball.vx = Math.sin(angle) * newSpeed;
            ball.vy = -Math.abs(Math.cos(angle) * newSpeed);
            
            // Force ball away from paddle
            if (paddle.isTop) {
                ball.y = paddle.y + paddle.height / 2 + ball.radius + 3;
                ball.vy = Math.abs(ball.vy); // Force downward
            } else {
                ball.y = paddle.y - paddle.height / 2 - ball.radius - 3;
                ball.vy = -Math.abs(ball.vy); // Force upward
            }
            
            // Add spin effect
            if (Math.abs(paddle.velocity) > 0.5) {
                ball.vx += paddle.velocity * 0.3;
            }
            
            return true;
        }
        return false;
    }
# --- FIM normaliza-escala-from-pdf ---

@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        # Pré-processamento: substitui múltiplos \n e quebras "duplas"
        clean_text = raw_text.replace("\r", "").replace("\n", " ")
        clean_text = " ".join(clean_text.split())  # remove múltiplos espaços
        lines = [clean_text[i:i+120] for i in range(0, len(clean_text), 120)]

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        for line in lines:
            pdf.multi_cell(w=190, h=8, txt=line)

        # CORREÇÃO: Tratamento adequado do output do FPDF
        pdf_output = pdf.output(dest='S')

        # Converte para bytes se necessário
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode('latin1')
        elif isinstance(pdf_output, bytearray):
            pdf_bytes = bytes(pdf_output)
        else:
            pdf_bytes = pdf_output

        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
