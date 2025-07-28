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

TURNOS = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE": {"inicio": "19:00", "fim": "07:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

def extrair_texto_pdf(pdf_file):
    texto_paginas = []
    with fitz.open(stream=pdf_file, filetype="pdf") as doc:
        for pagina in doc:
            texto_paginas.append(pagina.get_text())
    return texto_paginas

def identificar_unidade_e_setor(texto):
    unidade_escala = ""
    medico_setor = ""
    padrao_unidade = re.search(r"UNIDADE:\s*(.+)", texto)
    padrao_setor = re.search(r"UNIDADE SETOR:\s*(.+)", texto)

    if padrao_unidade:
        unidade_escala = padrao_unidade.group(1).strip()

    if padrao_setor:
        medico_setor = padrao_setor.group(1).strip()

    return unidade_escala, medico_setor

def obter_dias_mes(texto):
    linhas = texto.split("\n")
    for linha in linhas:
        dias = re.findall(r"\b\d{1,2}\b", linha)
        if len(dias) >= 28:
            return [int(d) for d in dias]
    return []

def normalizar_plantao(dia, entrada, saida, mes_ano, turno_label=None):
    data_inicio = datetime.strptime(f"{dia}/{mes_ano}", "%d/%m/%Y")
    if turno_label == "NOITE (início)":
        return {
            "dia": dia,
            "data": data_inicio.strftime("%d/%m/%Y"),
            "turno": "NOITE",
            "inicio": TURNOS["NOITE (início)"]["inicio"],
            "fim": TURNOS["NOITE (início)"]["fim"],
        }
    elif turno_label == "NOITE (fim)":
        data_inicio += timedelta(days=1)
        return {
            "dia": data_inicio.day,
            "data": data_inicio.strftime("%d/%m/%Y"),
            "turno": "NOITE",
            "inicio": TURNOS["NOITE (fim)"]["inicio"],
            "fim": TURNOS["NOITE (fim)"]["fim"],
        }
    else:
        return {
            "dia": dia,
            "data": data_inicio.strftime("%d/%m/%Y"),
            "turno": turno_label,
            "inicio": entrada,
            "fim": saida,
        }

def extrair_plantao_por_linha(linha, dias, mes_ano):
    plantoes = []
    for i, entrada in enumerate(linha):
        dia = dias[i] if i < len(dias) else None
        entrada = entrada.strip()

        if not entrada or entrada.upper() in {"FÉRIAS", "ATESTADO"}:
            continue

        if entrada == "N":
            plantoes.append(normalizar_plantao(dia, None, None, mes_ano, "NOITE (início)"))
            plantoes.append(normalizar_plantao(dia, None, None, mes_ano, "NOITE (fim)"))
        elif entrada == "n":
            plantoes.append(normalizar_plantao(dia, TURNOS["NOITE (início)"]["inicio"], TURNOS["NOITE (início)"]["fim"], mes_ano, "NOITE"))
        elif entrada == "M":
            plantoes.append(normalizar_plantao(dia, TURNOS["MANHÃ"]["inicio"], TURNOS["MANHÃ"]["fim"], mes_ano, "MANHÃ"))
        elif entrada == "T":
            plantoes.append(normalizar_plantao(dia, TURNOS["TARDE"]["inicio"], TURNOS["TARDE"]["fim"], mes_ano, "TARDE"))
        elif entrada == "D":
            plantoes.append(normalizar_plantao(dia, TURNOS["MANHÃ"]["inicio"], TURNOS["NOITE"]["fim"], mes_ano, "DIA TODO"))
        elif entrada == "PJ":
            plantoes.append(normalizar_plantao(dia, "07:00", "19:00", mes_ano, "PJ"))
        elif entrada == "PJ N":
            plantoes.append(normalizar_plantao(dia, TURNOS["NOITE (início)"]["inicio"], TURNOS["NOITE (início)"]["fim"], mes_ano, "PJ NOITE (início)"))
            plantoes.append(normalizar_plantao(dia, TURNOS["NOITE (fim)"]["inicio"], TURNOS["NOITE (fim)"]["fim"], mes_ano, "PJ NOITE (fim)"))

    return plantoes

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(file: UploadFile = File(...)):
    conteudo = await file.read()
    paginas = extrair_texto_pdf(conteudo)

    resultados = []

    for pagina_texto in paginas:
        unidade_escala, medico_setor = identificar_unidade_e_setor(pagina_texto)
        dias = obter_dias_mes(pagina_texto)

        match_mes = re.search(r"MÊS:\s*(\w+/\d{4})", pagina_texto)
        mes_ano = match_mes.group(1) if match_mes else "01/1900"

        linhas = pagina_texto.split("\n")
        for i, linha in enumerate(linhas):
            if re.match(r"^\d+\s+[A-Z ]{3,}", linha):
                colunas = linha.split()
                nome = " ".join(colunas[1:-5])
                crm = colunas[-1]
                cargo = colunas[-6]
                vinculo = colunas[-3]

                linha_plantao = linhas[i + 1].split() if i + 1 < len(linhas) else []
                plantoes = extrair_plantao_por_linha(linha_plantao, dias, mes_ano)

                resultados.append({
                    "medico_nome": nome,
                    "medico_crm": crm,
                    "medico_especialidade": cargo,
                    "medico_vinculo": vinculo,
                    "medico_setor": medico_setor,
                    "unidade_escala": unidade_escala,
                    "plantoes": plantoes
                })

    return resultados
    
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
