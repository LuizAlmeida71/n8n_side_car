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
import re
import base64
import fitz
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import Request
from fastapi.responses import JSONResponse
from fpdf import FPDF
import os
import traceback

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

def parse_mes_ano(text):
    """Extrai mês e ano do texto com múltiplos padrões"""
    patterns = [
        r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})',
        r'MÊS:\s*([A-ZÇÃ]+)/(\d{4})',
        r'MES/ANO:\s*([A-ZÇÃ]+)\s*/\s*(\d{4})',
        r'([A-ZÇÃ]+)/(\d{4})',
        r'([A-ZÇÃ]+)\s*(\d{4})'
    ]
    
    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            mes_nome, ano_str = match.groups()
            mes = MONTH_MAP.get(mes_nome.strip())
            if mes:
                try:
                    ano = int(ano_str)
                    return mes, ano
                except:
                    pass
    return None, None

def extract_unidade_setor_from_text(page_text):
    """
    Extrai UNIDADE e SETOR do texto, buscando em múltiplos formatos
    incluindo cabeçalhos de tabelas e texto acima das tabelas
    """
    unidade = None
    setor = None
    
    # Remove quebras de linha extras para facilitar a busca
    text_clean = page_text.replace('\n\n', '\n').replace('  ', ' ')
    
    # Padrões para UNIDADE - ordem de prioridade
    unidade_patterns = [
        # Padrão completo com dois pontos
        r'UNIDADE:\s*([^/\n]+?)(?:\s*UNIDADE[\s/_\-]*SETOR:|/|$|\n)',
        # Padrão em linha separada
        r'UNIDADE:\s*([^\n]+?)(?:\n|$)',
        # Padrão sem dois pontos
        r'UNIDADE\s+([^/\n]+?)(?:\s*UNIDADE[\s/_\-]*SETOR:|/|$|\n)',
        # Padrão genérico
        r'UNIDADE[:\s]*([^/\n]{5,}?)(?:/|$|\n)'
    ]
    
    # Padrões para SETOR - ordem de prioridade
    setor_patterns = [
        # Padrão com UNIDADE/SETOR ou UNIDADE SETOR
        r'UNIDADE[\s/_\-]*SETOR:\s*([^/\n]+?)(?:\s*/\s*RESPONSÁVEL|/|$|\n)',
        # Padrão em linha de cabeçalho de tabela
        r'UNIDADE\s*SETOR:\s*([^\n]+?)(?:\s*-\s*|/|$|\n)',
        # Padrão simplificado
        r'SETOR:\s*([^/\n]+?)(?:/|$|\n)',
        # Busca específica por UTI, EMERGÊNCIA, etc
        r'(?:SETOR:|UNIDADE.*SETOR:)\s*((?:UTI|EMERGÊNCIA|CENTRO|COORDENAÇÃO)[^/\n]*?)(?:/|$|\n)',
        # Padrão após hífen
        r'-\s*(UTI[^/\n]*\d*)(?:/|$|\n)',
        # Padrão genérico para capturar setores específicos
        r'(UNIDADE DE TERAPIA INTENSIVA[^/\n]*)',
        r'(COORDENAÇÃO[^/\n]+UTI[^/\n]*)'
    ]
    
    # Busca UNIDADE
    for pattern in unidade_patterns:
        match = re.search(pattern, text_clean, re.IGNORECASE | re.MULTILINE)
        if match:
            unidade_raw = match.group(1).strip()
            # Limpeza do valor
            unidade_raw = re.sub(r'UNIDADE[\s/_\-]*SETOR.*', '', unidade_raw, flags=re.IGNORECASE)
            unidade_raw = unidade_raw.strip(' -:/')
            if unidade_raw and len(unidade_raw) > 5:
                unidade = unidade_raw
                break
    
    # Busca SETOR
    for pattern in setor_patterns:
        match = re.search(pattern, text_clean, re.IGNORECASE | re.MULTILINE)
        if match:
            setor_raw = match.group(1).strip()
            # Limpeza do valor
            setor_raw = re.sub(r'RESPONSÁVEL\s*TÉCNICO.*', '', setor_raw, flags=re.IGNORECASE)
            setor_raw = re.sub(r'MÊS:.*', '', setor_raw, flags=re.IGNORECASE)
            setor_raw = setor_raw.strip(' -:/,')
            if setor_raw and len(setor_raw) > 3:
                setor = setor_raw
                break
    
    return unidade, setor

def is_header_row(row):
    """Identifica se uma linha é cabeçalho da tabela"""
    if not row or len(row) < 3:
        return False
    
    # Converte para texto para análise
    row_text = ' '.join([str(cell) for cell in row if cell]).upper()
    
    # Indicadores fortes de cabeçalho
    header_indicators = [
        'NOME COMPLETO', 'CARGO', 'MATRÍCULA', 'MATRICULA', 
        'VÍNCULO', 'VINCULO', 'CRM', 'CONSELHO', 
        'HORÁRIO', 'HORARIO', 'CH', 'C.H'
    ]
    
    # Conta indicadores presentes
    indicator_count = sum(1 for indicator in header_indicators if indicator in row_text)
    
    # Conta números de dias (1-31)
    day_count = 0
    for cell in row:
        if cell:
            try:
                val = int(str(cell).strip().replace('.', ''))
                if 1 <= val <= 31:
                    day_count += 1
            except:
                pass
    
    # É cabeçalho se tem indicadores E dias, ou muitos dias
    return (indicator_count >= 2 and day_count >= 5) or day_count >= 10

def is_multi_line_header(rows, start_idx):
    """Verifica se há um cabeçalho de múltiplas linhas"""
    if start_idx >= len(rows) - 1:
        return False, 0
    
    # Verifica se a próxima linha complementa o cabeçalho
    current_row = rows[start_idx]
    next_row = rows[start_idx + 1] if start_idx + 1 < len(rows) else []
    
    # Se a linha atual tem dias e a próxima tem horários, é multi-linha
    has_days = any(str(cell).isdigit() and 1 <= int(str(cell)) <= 31 
                   for cell in current_row if cell and str(cell).isdigit())
    
    has_schedule_info = any(cell and ('PLANTÃO' in str(cell).upper() or 
                                     'HORÁRIO' in str(cell).upper() or
                                     'HORARIO' in str(cell).upper() or
                                     ':00' in str(cell))
                           for cell in next_row)
    
    if has_days and has_schedule_info:
        return True, 2
    
    return False, 0

def build_header_map(rows, start_idx):
    """
    Constrói mapeamento de cabeçalho, lidando com múltiplas linhas
    """
    if not rows or start_idx >= len(rows):
        return {}, None, 1
    
    header_map = {}
    nome_idx = None
    
    # Verifica se é cabeçalho multi-linha
    is_multi, lines = is_multi_line_header(rows, start_idx)
    
    # Processa a linha principal do cabeçalho
    main_row = rows[start_idx]
    
    # Detecta e pula coluna numérica inicial se existir
    start_col = 0
    if len(main_row) > 0 and main_row[0]:
        first_val = str(main_row[0]).strip()
        if first_val.isdigit() or first_val in ['', 'Nº', '#', 'N°']:
            start_col = 1
    
    # Mapeia colunas
    for i, cell in enumerate(main_row[start_col:], start=start_col):
        if not cell:
            continue
            
        cell_text = str(cell).replace('\n', ' ').strip().upper()
        
        # Colunas de informação
        if any(x in cell_text for x in ['NOME', 'COMPLETO']):
            header_map["NOME COMPLETO"] = i
            nome_idx = i
        elif 'CARGO' in cell_text:
            header_map["CARGO"] = i
        elif 'VÍNCULO' in cell_text or 'VINCULO' in cell_text:
            header_map["VÍNCULO"] = i
        elif 'CRM' in cell_text or 'CONSELHO' in cell_text:
            header_map["CRM"] = i
        elif 'MATRÍCULA' in cell_text or 'MATRICULA' in cell_text:
            header_map["MATRÍCULA"] = i
        elif 'HORÁRIO' in cell_text or 'HORARIO' in cell_text:
            header_map["HORÁRIO"] = i
        elif cell_text in ['CH', 'C.H', 'C.H.']:
            header_map["CH"] = i
        else:
            # Tenta identificar dias
            try:
                day = int(cell_text.replace('.', '').replace(',', ''))
                if 1 <= day <= 31:
                    header_map[day] = i
            except:
                pass
    
    return header_map, nome_idx, lines

def clean_cell_value(value):
    """Limpa e normaliza valor de célula"""
    if not value:
        return ""
    
    # Remove quebras de linha e espaços extras
    cleaned = str(value).replace('\n', ' ').strip()
    # Remove espaços múltiplos
    cleaned = ' '.join(cleaned.split())
    
    return cleaned

def extract_professional_info(row, header_map):
    """Extrai informações do profissional da linha"""
    info = {}
    
    for key, idx in header_map.items():
        if isinstance(key, str) and idx < len(row) and row[idx]:
            value = clean_cell_value(row[idx])
            if value:
                info[key] = value
    
    return info

def is_valid_professional_name(name):
    """Valida se é um nome de profissional válido"""
    if not name or not isinstance(name, str):
        return False
    
    name_clean = clean_cell_value(name).upper()
    
    # Palavras que indicam que não é um nome
    invalid_keywords = [
        'NOME COMPLETO', 'NOME', 'CARGO', 'MATRÍCULA', 'MATRICULA',
        'HORÁRIO', 'HORARIO', 'CONSELHO', 'VÍNCULO', 'VINCULO',
        'UNIDADE', 'SETOR', 'MÊS', 'ANO', 'ESCALA', 'PLANTÃO',
        'LEGENDA', 'ASSINATURA', 'CH', 'C.H'
    ]
    
    # Verifica palavras inválidas
    for keyword in invalid_keywords:
        if keyword in name_clean:
            return False
    
    # Nome deve ter pelo menos 2 palavras ou ser maiúsculo com mais de 3 caracteres
    words = name_clean.split()
    if len(words) >= 2:
        return True
    elif len(words) == 1 and len(name_clean) > 3:
        # Aceita nomes únicos se parecerem nomes (não apenas siglas)
        return not name_clean.replace('.', '').replace('-', '').isdigit()
    
    return False

def interpretar_turno(token, setor=""):
    """Interpreta tokens de turno"""
    if not token or not isinstance(token, str):
        return []
    
    # Limpa o token
    token_clean = token.upper().replace('\n', '').replace('/', '').replace(' ', '').strip()
    turnos = []
    
    # Processa cada caractere
    for char in token_clean:
        if char == 'M':
            turnos.append({"turno": "MANHÃ"})
        elif char == 'T':
            turnos.append({"turno": "TARDE"})
        elif char == 'D':
            # Dia = Manhã + Tarde
            turnos.append({"turno": "MANHÃ"})
            turnos.append({"turno": "TARDE"})
        elif char == 'N':
            # Noite = início + fim
            turnos.append({"turno": "NOITE (início)"})
            turnos.append({"turno": "NOITE (fim)"})
        elif char == 'n':
            # n minúsculo = apenas início da noite
            turnos.append({"turno": "NOITE (início)"})
    
    return turnos

def dedup_plantao(plantoes):
    """Remove plantões duplicados"""
    seen = set()
    result = []
    
    for p in plantoes:
        key = (p["dia"], p["turno"], p["inicio"], p["fim"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    
    return result

def process_professional_shifts(rows, start_idx, header_map, nome_idx, mes, ano):
    """Processa os plantões de um profissional"""
    if start_idx >= len(rows):
        return None
    
    current_row = rows[start_idx]
    if not current_row or nome_idx >= len(current_row):
        return None
    
    # Extrai nome
    nome = clean_cell_value(current_row[nome_idx])
    if not is_valid_professional_name(nome):
        return None
    
    # Extrai informações básicas
    info = extract_professional_info(current_row, header_map)
    
    # Estrutura do profissional
    professional = {
        "medico_nome": nome,
        "medico_crm": info.get("CRM", "N/I"),
        "medico_especialidade": info.get("CARGO", "N/I"),
        "medico_vinculo": info.get("VÍNCULO", info.get("VINCULO", "N/I")),
        "plantoes_raw": defaultdict(list)
    }
    
    # Processa plantões da linha atual
    for dia, col_idx in header_map.items():
        if isinstance(dia, int) and 1 <= d
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
