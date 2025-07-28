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
    """Extrai mês e ano do texto"""
    patterns = [
        r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})',
        r'([A-ZÇÃ]+)/(\d{4})',
        r'MÊS:\s*([A-ZÇÃ]+)/(\d{4})',
        r'([A-ZÇÃ]+)\s*(\d{4})'
    ]
    
    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            mes_nome, ano_str = match.groups()
            mes = MONTH_MAP.get(mes_nome)
            if mes:
                ano = int(ano_str)
                return mes, ano
    return None, None

def extract_unidade_setor_from_text(page_text):
    """Extrai UNIDADE e SETOR considerando diferentes formatos"""
    unidade = None
    setor = None
    
    # Padrões para UNIDADE
    unidade_patterns = [
        r'UNIDADE:\s*([^/\n]+?)(?:\s*UNIDADE[\s/_\-]*SETOR:|$|\n)',
        r'UNIDADE:\s*([^\n]+?)(?:\s{2,}|\n)',
        r'UNIDADE:\s*([^/]+?)/',
        r'UNIDADE:\s*(.+?)(?=\s*MÊS:|$|\n)'
    ]
    
    # Padrões para SETOR - incluindo casos específicos de páginas de continuação
    setor_patterns = [
        r'UNIDADE[\s/_\-]*SETOR:\s*([^/\n]+?)(?:\s*/\s*RESPONSÁVEL|$|\n)',
        r'SETOR:\s*([^/\n]+?)(?:/|$|\n)',
        # Padrão específico para páginas de continuação com cabeçalho simplificado
        r'UNIDADE[\s:]*([^/\n]+?)(?:[\s/]+SETOR[\s:]*([^/\n]+?))?',
        # Busca isolada por UTI ou outros setores
        r'(UTI[^/\n]*\d*)',
        r'(CENTRO[^/\n]+)',
        r'(EMERGÊNCIA[^/\n]+)'
    ]
    
    # Tenta extrair UNIDADE
    for pattern in unidade_patterns:
        match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
        if match:
            unidade_raw = match.group(1).strip()
            unidade_raw = re.sub(r'UNIDADE[\s/_\-]*SETOR.*', '', unidade_raw, flags=re.IGNORECASE)
            if unidade_raw and len(unidade_raw) > 3:
                unidade = unidade_raw
                break
    
    # Tenta extrair SETOR
    for pattern in setor_patterns:
        match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
        if match:
            if match.lastindex and match.lastindex > 1:
                # Para padrões com múltiplos grupos
                setor_raw = match.group(2) if match.group(2) else match.group(1)
            else:
                setor_raw = match.group(1)
            
            if setor_raw:
                setor_raw = setor_raw.strip()
                setor_raw = re.sub(r'RESPONSÁVEL\s*TÉCNICO', '', setor_raw, flags=re.IGNORECASE)
                setor_raw = setor_raw.strip(' -:/,')
                if setor_raw and len(setor_raw) > 2:
                    setor = setor_raw
                    break
    
    return unidade, setor

def is_header_row(row):
    """Identifica se uma linha é cabeçalho da tabela"""
    if not row:
        return False
    
    row_text = ' '.join([str(cell) for cell in row if cell]).upper()
    
    # Indicadores de cabeçalho
    header_indicators = [
        'NOME COMPLETO', 'NOME', 'CARGO', 'MATRÍCULA', 'VÍNCULO', 'VINCULO',
        'CRM', 'CONSELHO', 'HORÁRIO', 'HORARIO', 'CH', 'C.H'
    ]
    
    # Verifica se tem pelo menos 2 indicadores
    count = sum(1 for indicator in header_indicators if indicator in row_text)
    
    # Ou se tem números sequenciais (dias do mês)
    numeric_count = sum(1 for cell in row if isinstance(cell, (int, float)) or 
                       (isinstance(cell, str) and cell.isdigit() and 1 <= int(cell) <= 31))
    
    return count >= 2 or numeric_count >= 5

def clean_cell_value(value):
    """Limpa o valor de uma célula"""
    if not value:
        return ""
    return str(value).replace('\n', ' ').strip()

def build_header_map(row):
    """Constrói mapeamento de colunas com melhor detecção"""
    if not row:
        return {}, None
    
    header_map = {}
    nome_idx = None
    
    # Processa cada coluna
    for i, col_name in enumerate(row):
        if not col_name:
            continue
            
        clean_name = clean_cell_value(col_name).upper()
        
        # Mapeamento de colunas principais
        if "NOME" in clean_name and "COMPLETO" in clean_name:
            header_map["NOME COMPLETO"] = i
            nome_idx = i
        elif clean_name == "NOME" and nome_idx is None:
            header_map["NOME COMPLETO"] = i
            nome_idx = i
        elif "CARGO" in clean_name:
            header_map["CARGO"] = i
        elif "VÍNCULO" in clean_name or "VINCULO" in clean_name:
            header_map["VÍNCULO"] = i
        elif "CONSELHO" in clean_name or "CRM" in clean_name:
            header_map["CRM"] = i
        elif "MATRÍCULA" in clean_name or "MATRICULA" in clean_name:
            header_map["MATRÍCULA"] = i
        elif "HORÁRIO" in clean_name or "HORARIO" in clean_name:
            header_map["HORÁRIO"] = i
        elif clean_name in ["CH", "C.H", "C.H."]:
            header_map["CH"] = i
        # Colunas numéricas (dias)
        else:
            try:
                day = int(clean_name.replace('.', '').replace(',', ''))
                if 1 <= day <= 31:
                    header_map[day] = i
            except:
                pass
    
    return header_map, nome_idx

def extract_info_from_row(row, header_map):
    """Extrai informações de uma linha baseado no mapeamento"""
    info = {}
    
    for key, idx in header_map.items():
        if isinstance(key, str) and idx < len(row):
            value = clean_cell_value(row[idx])
            if value:
                info[key] = value
    
    return info

def interpretar_turno(token, setor=""):
    """Interpreta tokens de turno"""
    if not token or not isinstance(token, str):
        return []
    
    token_clean = token.upper().replace('\n', '').replace('/', '').replace(' ', '').strip()
    turnos_finais = []
    
    # Processa cada caractere do token
    for t in token_clean:
        if t == 'M':
            turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T':
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'D':
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'N':
            turnos_finais.append({"turno": "NOITE (início)"})
            turnos_finais.append({"turno": "NOITE (fim)"})
        elif t == 'n':
            turnos_finais.append({"turno": "NOITE (início)"})
    
    return turnos_finais

def is_valid_professional_name(name):
    """Valida nome de profissional"""
    if not name or not isinstance(name, str):
        return False
    
    name_clean = clean_cell_value(name)
    name_upper = name_clean.upper()
    
    # Palavras que indicam que não é um nome
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", 
               "CARGO", "MATRÍCULA", "HORÁRIO", "CONSELHO", "VÍNCULO", "UNIDADE",
               "SETOR", "MÊS", "ANO"]
    
    if any(keyword in name_upper for keyword in ignored):
        return False
    
    # Deve ter pelo menos 2 palavras ou ser todo maiúsculo
    parts = name_clean.split()
    return len(parts) >= 2 or (len(parts) == 1 and name_clean.isupper() and len(name_clean) > 3)

def dedup_plantao(lista):
    """Remove plantões duplicados"""
    seen = set()
    result = []
    for p in lista:
        key = (p["dia"], p["turno"], p["inicio"], p["fim"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        all_pages_data = []
        
        # Variáveis globais para manter valores entre páginas
        global_unidade = None
        global_setor = None
        global_mes = None
        global_ano = None
        
        # Primeira passada: extrai dados básicos de todas as páginas
        for page_idx, page_data in enumerate(body):
            b64_data = page_data.get("bae64")
            if not b64_data:
                continue
            
            pdf_bytes = base64.b64decode(b64_data)
            
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                
                # Extrai unidade e setor
                unidade, setor = extract_unidade_setor_from_text(page_text)
                mes, ano = parse_mes_ano(page_text)
                
                # Atualiza valores globais
                if unidade:
                    global_unidade = unidade
                if setor:
                    global_setor = setor
                if mes:
                    global_mes = mes
                if ano:
                    global_ano = ano
                
                # Extrai tabelas
                tables_data = []
                for table in page.find_tables():
                    extracted = table.extract()
                    if extracted:
                        tables_data.extend(extracted)
                
                all_pages_data.append({
                    "page_idx": page_idx,
                    "unidade": unidade or global_unidade,
                    "setor": setor or global_setor,
                    "tables": tables_data,
                    "text": page_text
                })
        
        if not global_mes or not global_ano:
            return JSONResponse(content={"error": "Mês/Ano não encontrados"}, status_code=400)
        
        # Processa todas as páginas
        todos_profissionais = []
        
        for page_data in all_pages_data:
            page_idx = page_data["page_idx"]
            unidade = page_data["unidade"] or "NÃO INFORMADO"
            setor = page_data["setor"] or "NÃO INFORMADO"
            tables = page_data["tables"]
            
            # Processa tabelas da página
            profissionais_pagina = processar_tabelas_pagina(
                tables, unidade, setor, global_mes, global_ano, page_idx
            )
            
            todos_profissionais.extend(profissionais_pagina)
        
        # Resultado final
        mes_nome = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(global_mes)]
        
        return JSONResponse(content=[{
            "unidade_escala": global_unidade or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome}/{global_ano}",
            "profissionais": todos_profissionais
        }])
        
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "trace": traceback.format_exc()}, 
            status_code=500
        )

def processar_tabelas_pagina(tables, unidade, setor, mes, ano, page_idx):
    """Processa todas as tabelas de uma página"""
    profissionais = []
    current_header_map = None
    current_nome_idx = None
    profissionais_temp = {}
    
    for row_idx, row in enumerate(tables):
        # Verifica se é cabeçalho
        if is_header_row(row):
            current_header_map, current_nome_idx = build_header_map(row)
            continue
        
        # Se não tem cabeçalho válido, pula
        if not current_header_map or current_nome_idx is None:
            continue
        
        # Extrai nome
        nome = None
        if current_nome_idx < len(row):
            nome_raw = row[current_nome_idx]
            if nome_raw and is_valid_professional_name(nome_raw):
                nome = clean_cell_value(nome_raw)
        
        if not nome:
            continue
        
        # Se é um novo profissional, cria entrada
        if nome not in profissionais_temp:
            info = extract_info_from_row(row, current_header_map)
            
            profissionais_temp[nome] = {
                "medico_nome": nome,
                "medico_crm": info.get("CRM", "N/I"),
                "medico_especialidade": info.get("CARGO", "N/I"),
                "medico_vinculo": info.get("VÍNCULO", "N/I"),
                "medico_setor": setor,
                "plantoes_raw": defaultdict(list),
                "header_map": current_header_map
            }
        
        # Extrai plantões da linha
        prof_data = profissionais_temp[nome]
        for dia, col_idx in prof_data["header_map"].items():
            if isinstance(dia, int) and 1 <= dia <= 31 and col_idx < len(row):
                cell_value = row[col_idx]
                if cell_value:
                    cell_clean = clean_cell_value(cell_value)
                    if cell_clean:
                        prof_data["plantoes_raw"][dia].append(cell_clean)
    
    # Converte profissionais temporários para formato final
    for nome, prof_data in profissionais_temp.items():
        plantoes = []
        
        for dia, tokens in prof_data["plantoes_raw"].items():
            for token in tokens:
                turnos = interpretar_turno(token, prof_data["medico_setor"])
                
                for turno_info in turnos:
                    horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                    data_plantao = datetime(ano, mes, dia)
                    
                    if turno_info["turno"] == "NOITE (fim)":
                        data_fim = data_plantao + timedelta(days=1)
                        plantoes.append({
                            "dia": data_fim.day,
                            "data": data_fim.strftime('%d/%m/%Y'),
                            "turno": turno_info["turno"],
                            "inicio": horarios.get("inicio"),
                            "fim": horarios.get("fim")
                        })
                    else:
                        plantoes.append({
                            "dia": data_plantao.day,
                            "data": data_plantao.strftime('%d/%m/%Y'),
                            "turno": turno_info["turno"],
                            "inicio": horarios.get("inicio"),
                            "fim": horarios.get("fim")
                        })
        
        # Remove duplicados e ordena
        plantoes = dedup_plantao(plantoes)
        plantoes.sort(key=lambda p: (p["dia"], p["inicio"] or ""))
        
        if plantoes:  # Só adiciona se tiver plantões
            profissional_final = {
                "medico_nome": prof_data["medico_nome"],
                "medico_crm": prof_data["medico_crm"],
                "medico_especialidade": prof_data["medico_especialidade"],
                "medico_vinculo": prof_data["medico_vinculo"],
                "medico_setor": prof_data["medico_setor"],
                "plantoes": plantoes
            }
            profissionais.append(profissional_final)
    
    return profissionais
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
