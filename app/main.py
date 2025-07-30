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


@app.post("/split-pdf-base64")
async def split_pdf_base64(request: Request):
    try:
        body = await request.json()
        b64 = body.get("base64")
        if not b64:
            return JSONResponse(content={"error": "Campo 'base64' ausente"}, status_code=400)

        pdf_bytes = base64.b64decode(b64)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_b64 = []

        for i in range(len(doc)):
            single_page = fitz.open()
            single_page.insert_pdf(doc, from_page=i, to_page=i)

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

HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

def parse_mes_ano(text):
    match = re.search(r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    return MONTH_MAP.get(mes_nome), int(ano_str)

def interpretar_turno(token):
    token = token.replace('\n', '').replace('/', '').replace(' ', '').upper()
    turnos_finais = []
    for t in token:
        if t == 'M': turnos_finais.append("MANHÃ")
        elif t == 'T': turnos_finais.append("TARDE")
        elif t == 'D': turnos_finais.extend(["MANHÃ", "TARDE"])
        elif t == 'N': turnos_finais.extend(["NOITE (início)", "NOITE (fim)"])
        elif t == 'n': turnos_finais.append("NOITE (início)")
    return turnos_finais

def is_valid_professional_name(name):
    if not name or not isinstance(name, str): return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", "CARGO", "MATRÍCULA", "UNIDADE", "SETOR", "MÊS", "ESCALA", "ÚLTIMA"]
    if any(keyword in name_upper for keyword in ignored): return False
    return len(name.strip().split()) >= 2

def extrair_metadados_pagina(page_text):
    unidade = re.search(r'UNIDADE[:\s-]*(.+?)(UNIDADE|SETOR|MÊS|ESCALA|$)', page_text.replace('\n', ' '), re.I)
    setor = re.search(r'UNIDADE[/\s-]*SETOR[:\s-]*(.+?)(MÊS|ESCALA|$)', page_text.replace('\n', ' '), re.I)
    return unidade.group(1).strip() if unidade else None, setor.group(1).strip() if setor else None

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        pages = body["pages"] if isinstance(body, dict) else body
        all_table_rows, last_unidade, last_setor, last_mes, last_ano = [], None, None, None, None

        for page_data in pages:
            b64_data = page_data.get("file_base64") or page_data.get("bae64") or page_data.get("base64")
            if not b64_data: continue

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
                if tabelas:
                    tabela = tabelas[0]
                    all_table_rows.extend(tabela)

        header_row_idx = next((i for i, row in enumerate(all_table_rows) if row and "NOME COMPLETO" in ''.join([str(cell or '') for cell in row]).upper()), None)
        if header_row_idx is None:
            return JSONResponse({"error": "Cabeçalho não encontrado."}, status_code=400)

        dias_row = all_table_rows[header_row_idx + 1]
        header_map = {idx: col.strip() for idx, col in enumerate(dias_row) if isinstance(col, (str, int)) and str(col).strip().isdigit()}
        nome_idx = next(idx for idx, col in enumerate(all_table_rows[header_row_idx]) if "NOME COMPLETO" in str(col).upper())

        profissionais_data = defaultdict(lambda: defaultdict(list))
        for row in all_table_rows[header_row_idx+2:]:
            nome_bruto = row[nome_idx]
            if not is_valid_professional_name(nome_bruto): continue
            nome = ' '.join(nome_bruto.split())

            for idx, dia in header_map.items():
                if idx < len(row) and row[idx]:
                    turnos = interpretar_turno(row[idx])
                    for turno in turnos:
                        horarios = HORARIOS_TURNO.get(turno)
                        dia_plantao = datetime(last_ano, last_mes, int(dia))
                        if turno == "NOITE (fim)": dia_plantao += timedelta(days=1)
                        profissionais_data[nome]["plantoes"].append({
                            "dia": dia_plantao.day,
                            "data": dia_plantao.strftime('%d/%m/%Y'),
                            "turno": turno,
                            "inicio": horarios["inicio"],
                            "fim": horarios["fim"]
                        })

        lista_profissionais_final = [{
            "medico_nome": nome,
            "medico_setor": last_setor or "NÃO INFORMADO",
            "plantoes": sorted(plantoes["plantoes"], key=lambda x: (datetime.strptime(x["data"], '%d/%m/%Y'), x["inicio"]))
        } for nome, plantoes in profissionais_data.items()]

        mes_nome_str = [k for k, v in MONTH_MAP.items() if v == last_mes][0]
        return JSONResponse([{
            "unidade_escala": last_unidade or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome_str}/{last_ano}",
            "profissionais": lista_profissionais_final
        }])

    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)

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





# --- INICIO normaliza-escala-PACS ---
# --- Constantes e Mapas ---
MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 'MAIO': 5,
    'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10,
    'NOVEMBRO': 11, 'DEZEMBRO': 12
}

HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

def parse_mes_ano(text: str):
    month_regex = '|'.join(MONTH_MAP.keys())
    match = re.search(
        r'(?:MÊS\s*(?:DE)?\s*)?(' + month_regex + r')\s*(?:DE\s*|[/|-]?)\s*(\d{4})',
        text.upper()
    )
    if not match:
        return None, None
    mes_nome, ano_str = match.groups()
    return MONTH_MAP.get(mes_nome.upper()), int(ano_str)

def interpretar_turno(token: str):
    token_clean = token.replace('\n', '').replace('/', '').replace(' ', '')
    tokens = list(token_clean)
    turnos = []
    for t in tokens:
        if t == 'M': turnos.append("MANHÃ")
        elif t == 'T': turnos.append("TARDE")
        elif t == 'D': turnos.extend(["MANHÃ", "TARDE"])
        elif t == 'N': turnos.extend(["NOITE (início)", "NOITE (fim)"])
        elif t == 'n': turnos.append("NOITE (início)")
    return turnos

def is_valid_professional_name(name: str):
    if not name or not isinstance(name, str):
        return False
    upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "CARGO", "MATRÍCULA"]
    return not any(k in upper for k in ignored) and (len(name.split()) >= 2 or name.isupper())

def dedup_plantao(plantoes):
    seen = set()
    result = []
    for p in plantoes:
        key = (p["dia"], p["turno"], p["inicio"], p["fim"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

@app.post("/normaliza-escala-PACS")
async def normaliza_escala_PACS(request: Request):
    try:
        body = await request.json()
        full_text = ""
        all_table_rows = []
        last_unidade, last_setor = None, None
        last_mes, last_ano = None, None

        # Pega textos e tabelas de todas as páginas
        for page in body:
            b64 = page.get("base64") or page.get("bae64")
            if not b64:
                continue
            pdf_bytes = base64.b64decode(b64)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page_text = doc[0].get_text("text")
                full_text += page_text + "\n"
                for table in doc[0].find_tables():
                    if table.extract():
                        all_table_rows.extend(table.extract())

        # Extrai metadados
        unidade = re.search(r'UNIDADE:\s*(.*?)\n', full_text, re.IGNORECASE)
        setor = re.search(r'SETOR:\s*(.*?)\n', full_text, re.IGNORECASE)
        last_unidade = unidade.group(1).strip() if unidade else "NÃO INFORMADO"
        last_setor = setor.group(1).strip() if setor else "NÃO INFORMADO"
        last_mes, last_ano = parse_mes_ano(full_text)
        if last_mes is None or last_ano is None:
            return JSONResponse(content={"error": "Mês/Ano não encontrados."}, status_code=400)

        # Processa profissionais
        profissionais = defaultdict(lambda: {"rows": []})
        header_map, nome_idx = None, None
        last_name = None

        for row in all_table_rows:
            if any("NOME COMPLETO" in str(cell).upper() for cell in row):
                header_map = {}
                offset = 1 if str(row[0]).strip().isdigit() else 0
                for i, cell in enumerate(row[offset:]):
                    name = str(cell).upper().strip()
                    col_idx = i + offset
                    if "NOME COMPLETO" in name: header_map["NOME"] = col_idx
                    elif "CRM" in name: header_map["CRM"] = col_idx
                    elif "CARGO" in name: header_map["CARGO"] = col_idx
                    elif "VÍNCULO" in name or "VINCULO" in name: header_map["VINCULO"] = col_idx
                    elif name.isdigit(): header_map[int(name)] = col_idx
                nome_idx = header_map.get("NOME")
                continue

            if not header_map or nome_idx is None:
                continue

            nome = row[nome_idx] if nome_idx < len(row) else None
            if nome and is_valid_professional_name(nome):
                last_name = nome.replace('\n', ' ').strip()
            elif nome and last_name and len(nome.strip().split()) == 1:
                last_name = f"{last_name} {nome.strip()}"

            if last_name:
                new_row = list(row)
                new_row[nome_idx] = last_name
                profissionais[last_name]["rows"].append(new_row)

        # Monta resposta
        saida = []
        for nome, info in profissionais.items():
            linha = info["rows"][0]
            get_val = lambda campo, default="": linha[header_map[campo]].strip() if campo in header_map and header_map[campo] < len(linha) else default

            vinculo = get_val("VINCULO").upper()
            if "PAES" not in vinculo:
                continue

            plantoes = []
            for row in info["rows"]:
                for dia, idx in header_map.items():
                    if isinstance(dia, int) and idx < len(row):
                        token = str(row[idx]).strip()
                        if not token:
                            continue
                        for turno in interpretar_turno(token):
                            horarios = HORARIOS_TURNO.get(turno, {})
                            data = datetime(last_ano, last_mes, dia)
                            if turno == "NOITE (fim)":
                                data += timedelta(days=1)
                            plantoes.append({
                                "data": data.strftime("%d/%m/%Y"),
                                "dia": data.day,
                                "turno": turno,
                                "setor": last_setor,
                                "inicio": horarios.get("inicio"),
                                "fim": horarios.get("fim")
                            })

            if not plantoes:
                continue

            saida.append({
                "medico_nome": nome,
                "medico_crm": get_val("CRM"),
                "medico_especialidade": get_val("CARGO"),
                "medico_vinculo": get_val("VINCULO"),
                "medico_setor": last_setor,
                "medico_unidade": last_unidade,
                "plantoes": sorted(dedup_plantao(plantoes), key=lambda p: (p["dia"], p["inicio"]))
            })

        mes_str = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(last_mes)]
        return JSONResponse(content=[{
            "unidade_escala": last_unidade,
            "mes_ano_escala": f"{mes_str}/{last_ano}",
            "profissionais": saida
        }])

    except Exception as e:
        return JSONResponse(content={
            "error": str(e),
            "trace": traceback.format_exc()
        }, status_code=500)


# --- FIM normaliza-escala-PACS ---
