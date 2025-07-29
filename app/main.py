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
# --- CONSTANTES ---

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

# --- FUNÇÕES AUXILIARES GERAIS ---

def clean_cell_value(value):
    if not value: return ""
    return ' '.join(str(value).replace('\n', ' ').split())

def is_header_row(row):
    if not row or len(row) < 3: return False
    row_text = ' '.join([str(cell) for cell in row if cell]).upper()
    header_indicators = ['NOME', 'CARGO', 'MATRÍCULA', 'VÍNCULO', 'CONSELHO', 'HORÁRIO', 'C.H']
    indicator_count = sum(1 for indicator in header_indicators if indicator in row_text)
    day_count = sum(1 for cell in row if str(cell).strip().isdigit() and 1 <= int(str(cell).strip()) <= 31)
    return indicator_count >= 3 or day_count >= 15

def is_valid_professional_name(name):
    if not name or not isinstance(name, str) or len(name.strip()) < 4: return False
    name_clean = name.upper().strip()
    invalid_keywords = [
        'NOME COMPLETO', 'CARGO', 'MATRÍCULA', 'HORÁRIO', 'LEGENDA', 'ASSINATURA', 
        'UNIDADE', 'SETOR', 'MÊS', 'ANO', 'ALTERAÇÃO', 'GOVERNO', 'SECRETARIA',
        'ESCALA', 'PLANTÃO'
    ]
    if any(keyword in name_clean for keyword in invalid_keywords): return False
    if name_clean.replace('.', '').replace('-', '').isdigit(): return False
    return len(name_clean.split()) >= 2

def build_header_map(row):
    header_map, nome_idx = {}, None
    start_col = 1 if row and row[0] and (str(row[0]).isdigit() or str(row[0]).strip() in ['#', 'Nº']) else 0
    for i, cell in enumerate(row[start_col:], start=start_col):
        if not cell: continue
        cell_text = clean_cell_value(cell).upper()
        if 'NOME' in cell_text: nome_idx = header_map["NOME COMPLETO"] = i
        elif 'CARGO' in cell_text: header_map["CARGO"] = i
        elif 'VÍNCULO' in cell_text or 'VINCULO' in cell_text: header_map["VÍNCULO"] = i
        elif 'CRM' in cell_text or 'CONSELHO' in cell_text: header_map["CRM"] = i
        elif 'MATRÍCULA' in cell_text: header_map["MATRÍCULA"] = i
        elif 'HORÁRIO' in cell_text: header_map["HORÁRIO"] = i
        elif cell_text in ['CH', 'C.H', 'C.H.']: header_map["CH"] = i
        else:
            try:
                day = int(cell_text)
                if 1 <= day <= 31: header_map[day] = i
            except (ValueError, TypeError): pass
    return header_map, nome_idx

# --- BASE DE PADRÕES ---

def _default_professional_extractor(rows, start_idx, header_map, nome_idx, mes, ano):
    """Função genérica de extração que lida com linhas múltiplas por profissional. Serve como base para a maioria dos padrões."""
    current_row = rows[start_idx]
    nome = clean_cell_value(current_row[nome_idx])
    info = {key: clean_cell_value(current_row[idx]) for key, idx in header_map.items() if isinstance(key, str) and idx < len(current_row)}
    professional = {"medico_nome": nome, "medico_crm": info.get("CRM", "N/I"), "medico_especialidade": info.get("CARGO", "N/I"), "medico_vinculo": info.get("VÍNCULO", "N/I"), "plantoes_raw": defaultdict(list)}
    
    idx = start_idx
    while idx < len(rows):
        row_to_process = rows[idx]
        if idx > start_idx:
            next_name = clean_cell_value(row_to_process[nome_idx]) if nome_idx < len(row_to_process) else None
            if next_name and is_valid_professional_name(next_name): break
        for dia, col_idx in header_map.items():
            if isinstance(dia, int) and col_idx < len(row_to_process) and row_to_process[col_idx]:
                token = clean_cell_value(row_to_process[col_idx])
                if token: professional["plantoes_raw"][dia].append(token)
        idx += 1
        
    plantoes_final = []
    for dia, tokens in professional["plantoes_raw"].items():
        for token in set(tokens):
            turnos = []
            token_clean = token.upper().replace('/', '').replace(' ', '')
            if 'M' in token_clean: turnos.append({"turno": "MANHÃ"})
            if 'T' in token_clean: turnos.append({"turno": "TARDE"})
            if 'D' in token_clean:
                turnos.append({"turno": "MANHÃ"}); turnos.append({"turno": "TARDE"})
            if 'N' in token_clean:
                turnos.append({"turno": "NOITE (início)"}); turnos.append({"turno": "NOITE (fim)"})
            
            for turno_info in turnos:
                horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                try:
                    data_plantao = datetime(ano, mes, dia)
                    if turno_info["turno"] == "NOITE (fim)": data_plantao += timedelta(days=1)
                    plantoes_final.append({"dia": data_plantao.day, "data": data_plantao.strftime('%d/%m/%Y'), "turno": turno_info["turno"], "inicio": horarios.get("inicio"), "fim": horarios.get("fim")})
                except ValueError: continue
    
    seen = set()
    plantoes_dedup = [p for p in plantoes_final if tuple(p.items()) not in seen and not seen.add(tuple(p.items()))]
    professional["plantoes"] = sorted(plantoes_dedup, key=lambda p: (datetime.strptime(p["data"], '%d/%m/%Y'), p.get("inicio", "")))
    del professional["plantoes_raw"]
    return professional, idx

# --- Padrão 1: UTI HGR (Geral) ---
def detect_uti_hgr_pattern(page_text, header_row):
    text = page_text.upper()
    return "UNIDADE DE TERAPIA INTENSIVA" in text and "ÚLTIMA ALTERAÇÃO" in text

# --- Padrão 2: OPO Sobreaviso ---
def detect_opo_sobreaviso_pattern(page_text, header_row):
    text = page_text.upper()
    return "OPO - ORGANIZAÇÃO DE PROCURA DE ÓRGÃOS" in text or "CET-RR / OPO - RR" in text

# --- Padrão 3: HGR Clínica Médica / Neurocirurgia ---
def detect_hgr_clinica_medica_pattern(page_text, header_row):
    text = page_text.upper()
    header_text = ' '.join(clean_cell_value(c) for c in header_row).upper()
    return "NEUROCIRURGIA" in text or "TOTAL" in header_text

PATTERN_DATABASE = [
    {"name": "UTI_HGR_Geral", "detector": detect_uti_hgr_pattern, "extractor": _default_professional_extractor},
    {"name": "OPO_Sobreaviso", "detector": detect_opo_sobreaviso_pattern, "extractor": _default_professional_extractor},
    {"name": "HGR_Clinica_Medica", "detector": detect_hgr_clinica_medica_pattern, "extractor": _default_professional_extractor},
]

def find_best_pattern(page_text, header_row):
    for pattern in PATTERN_DATABASE:
        if pattern["detector"](page_text, header_row):
            return pattern
    return None

# --- ENDPOINT PRINCIPAL DA API ---

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        global_unidade, global_setor, global_mes, global_ano = None, None, None, None
        pages_content = []

        # --- PASSADA 1: Coleta de dados globais e conteúdo bruto ---
        for page_data in body:
            pdf_bytes = base64.b64decode(page_data.get("bae64"))
            page_rows = []
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                unidade, setor = extract_unidade_setor_from_text(page_text)
                mes, ano = parse_mes_ano(page_text)
                if unidade: global_unidade = unidade
                if setor: global_setor = setor
                if mes: global_mes = mes
                if ano: global_ano = ano
                for table in page.find_tables():
                    if table.extract():
                        page_rows.extend(table.extract())
            pages_content.append({"rows": page_rows, "text": page_text, "setor_pagina": setor})

        if not global_mes or not global_ano:
            return JSONResponse(content={"error": "Mês/Ano não puderam ser determinados."}, status_code=400)

        # --- PASSADA 2: Processamento com estado persistente de padrão ---
        all_professionals_map = {}
        current_context = {"pattern": None, "header_map": None, "nome_idx": None}

        for page in pages_content:
            all_rows = page["rows"]
            page_text = page["text"]
            setor_a_usar = page["setor_pagina"] or global_setor or "NÃO INFORMADO"
            
            i = 0
            while i < len(all_rows):
                row = all_rows[i]
                if is_header_row(row):
                    detected_pattern = find_best_pattern(page_text, row)
                    if detected_pattern:
                        header_map, nome_idx = build_header_map(row)
                        current_context = {"pattern": detected_pattern, "header_map": header_map, "nome_idx": nome_idx}
                    # Se não detectar, mantém o contexto anterior (página de continuação com cabeçalho repetido)
                
                if not current_context["pattern"]:
                    i += 1
                    continue

                header_map, nome_idx = current_context["header_map"], current_context["nome_idx"]
                if not header_map or nome_idx is None or nome_idx >= len(row):
                    i += 1
                    continue

                nome_raw = row[nome_idx]
                if is_valid_professional_name(nome_raw):
                    extractor_func = current_context["pattern"]["extractor"]
                    professional, next_i = extractor_func(all_rows, i, header_map, nome_idx, global_mes, global_ano)
                    
                    nome_key = professional["medico_nome"]
                    if nome_key not in all_professionals_map:
                        professional["medico_setor"] = setor_a_usar
                        all_professionals_map[nome_key] = professional
                    else:
                        existing = all_professionals_map[nome_key]
                        new_plantoes = professional["plantoes"]
                        existing["plantoes"].extend(p for p in new_plantoes if p not in existing["plantoes"])
                        existing["plantoes"].sort(key=lambda p: (datetime.strptime(p["data"], '%d/%m/%Y'), p.get("inicio", "")))
                    i = next_i
                else:
                    i += 1
        
        mes_nome = [k for k, v in MONTH_MAP.items() if v == global_mes][0]
        result = [{"unidade_escala": global_unidade or "NÃO INFORMADO", "mes_ano_escala": f"{mes_nome}/{global_ano}", "profissionais": list(all_professionals_map.values())}]
        return JSONResponse(content=result)
        
    except Exception as e:
        # Funções de baixo nível para extrair mês/ano e unidade/setor
        def parse_mes_ano(text):
            patterns = [r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', r'MES/ANO:\s*([A-ZÇÃ]+)\s*/\s*(\d{4})', r'([A-ZÇÃ]+)/(\d{4})', r'MÊS:\s*([A-ZÇÃ]+)', r'([A-ZÇÃ]+)\s+(\d{4})']
            text_upper = text.upper()
            mes, ano = None, None
            for pattern in patterns:
                match = re.search(pattern, text_upper)
                if match:
                    groups = match.groups()
                    mes_val = MONTH_MAP.get(groups[0].strip())
                    if mes_val: mes = mes_val
                    if len(groups) > 1 and groups[1]:
                        try: ano = int(groups[1])
                        except (ValueError, TypeError): pass
                    if mes and ano: return mes, ano
            if not ano:
                year_match = re.search(r'\b(20\d{2})\b', text)
                if year_match: ano = int(year_match.group(1))
            return mes, ano

        def extract_unidade_setor_from_text(page_text):
            unidade, setor = None, None
            unidade_patterns = [r'UNIDADE:\s*([^/\n]+?)(?:\s*UNIDADE\s*SETOR:|/|$)', r'UNIDADE CENTRAL\s*([^/\n]+?)(?:\s*UNIDADE\s*SETOR:|/|$)']
            setor_patterns = [r'UNIDADE\s*/\s*SETOR:\s*([^/\n]+?)(?:/|$)', r'UNIDADE\s*SETOR:\s*([^/\n]+?)(?:/|RESPONSÁVEL TÉCNICO|$)', r'SETOR:\s*CE[T|P][^/\n]+']
            for pattern in unidade_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    unidade = match.group(1).strip().replace('SETOR:', '').strip()
                    break
            for pattern in setor_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    setor = match.group(1).strip().replace('RESPONSÁVEL TÉCNICO', '').strip(' /')
                    break
            if not unidade:
                match = re.search(r'UNIDADE:\s*([^\n]+)', page_text, re.IGNORECASE)
                if match: unidade = match.group(1).strip()
            if not setor:
                match = re.search(r'SETOR:\s*([^\n]+)', page_text, re.IGNORECASE)
                if match: setor = match.group(1).strip()
            return unidade, setor
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
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
