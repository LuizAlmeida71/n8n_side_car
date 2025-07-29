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
MONTH_MAP = {m: i+1 for i, m in enumerate(['JANEIRO', 'FEVEREIRO', 'MARÇO', 'ABRIL', 'MAIO', 'JUNHO', 'JULHO', 'AGOSTO', 'SETEMBRO', 'OUTUBRO', 'NOVEMBRO', 'DEZEMBRO'])}

HEADER_PATTERNS = {
    "padrao_1": ["NOME COMPLETO", "CARGO", "MATRÍCULA", "VÍNCULO", "CH", "HORÁRIO", "CONSELHO DE CLASSE"]
}

def parse_mes_ano(text):
    match = re.search(r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    return MONTH_MAP.get(mes_nome), int(ano_str)

def interpretar_turno(token):
    mapa = {
        "M": [("07:00", "13:00")],
        "T": [("13:00", "19:00")],
        "N": [("19:00", "01:00"), ("01:00", "07:00")],
        "D": [("07:00", "13:00"), ("13:00", "19:00")]
    }
    token = token.replace('/', '').upper()
    turnos = []
    for char in token:
        if char in mapa:
            for inicio, fim in mapa[char]:
                turnos.append({"turno": char, "inicio": inicio, "fim": fim})
    return turnos

def normalizar_tabela(tabela):
    max_cols = max(len(row) for row in tabela)
    return [row + [""]*(max_cols - len(row)) for row in tabela]

def header_similarity(header_row, pattern):
    matches = sum(a.strip().upper() == b for a, b in zip(header_row, pattern))
    return matches / max(len(pattern), len(header_row))

def find_pattern(header_row):
    best_pattern, best_score = None, 0
    for name, pattern in HEADER_PATTERNS.items():
        score = header_similarity(header_row, pattern)
        if score > best_score:
            best_score, best_pattern = score, name
    return best_pattern if best_score > 0.6 else None

def extrair_metadados_pagina(page_text):
    unidade = re.search(r'UNIDADE[:\s-]*(.+?)(UNIDADE|SETOR|MÊS|ESCALA|$)', page_text.replace('\n', ' '), re.I)
    setor = re.search(r'UNIDADE[/\s-]*SETOR[:\s-]*(.+?)(MÊS|ESCALA|$)', page_text.replace('\n', ' '), re.I)
    return unidade.group(1).strip() if unidade else None, setor.group(1).strip() if setor else None

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        pages = body["pages"] if isinstance(body, dict) else body
        last_pattern_name = last_header_row = last_unidade = last_setor = None
        last_mes = last_ano = None
        all_rows = []

        for page_data in pages:
            b64_data = page_data.get("file_base64") or page_data.get("bae64") or page_data.get("base64")
            pdf_bytes = base64.b64decode(b64_data)

            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page_text = doc[0].get_text("text")
                unidade, setor = extrair_metadados_pagina(page_text)
                if unidade: last_unidade = unidade
                if setor: last_setor = setor
                mes, ano = parse_mes_ano(page_text)
                if mes: last_mes = mes
                if ano: last_ano = ano

                tabelas = [t.extract() for t in doc[0].find_tables() if t.extract()]
                if not tabelas: continue
                tabela = normalizar_tabela(tabelas[0])

                header_row = next((row for row in tabela if "NOME COMPLETO" in ''.join(row).upper()), None)
                if header_row:
                    pattern_name = find_pattern(header_row) or f"novo_{len(HEADER_PATTERNS)+1}"
                    if pattern_name.startswith("novo"):
                        HEADER_PATTERNS[pattern_name] = header_row
                    last_pattern_name, last_header_row = pattern_name, header_row
                    idx = tabela.index(header_row)
                    all_rows += tabela[idx+1:]
                else:
                    all_rows += tabela

        if not last_pattern_name: return JSONResponse({"error": "Padrão não detectado."}, 400)

        profissionais = []
        for row in all_rows:
            nome = row[0].strip()
            if nome:
                profissionais.append({"medico_nome": nome, "setor": last_setor, "plantoes": sorted(interpretar_turno(''.join(row[1:])), key=lambda x: x["inicio"])})

        mes_nome = [k for k,v in MONTH_MAP.items() if v == last_mes][0]
        return JSONResponse([{ "unidade": last_unidade, "setor": last_setor, "mes_ano": f"{mes_nome}/{last_ano}", "profissionais": profissionais }])

    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, 500)

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
