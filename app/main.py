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
import traceback

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

# --- FUNÇÕES AUXILIARES ---
def parse_mes_ano(text):
    patterns = [
        r'MÊS/ANO:\s*([A-ZÇÃ]+)\s*/\s*(\d{4})',
        r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})',
        r'MÊS:\s*([A-ZÇÃ]+)\s*/\s*(\d{4})',
        r'(?:MÊS:\s*|MÊS\s+)([A-ZÇÃ]+)\s+(\d{4})'
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
                except (ValueError, TypeError):
                    pass
    return None, None

def extract_unidade_setor_from_text(page_text):
    unidade, setor = None, None
    unidade_patterns = [
        r'UNIDADE:\s*([^/\n]+?)(?:\s*UNIDADE\s*SETOR:|/|$)',
        r'UNIDADE CENTRAL\s*([^/\n]+?)(?:\s*UNIDADE\s*SETOR:|/|$)'
    ]
    setor_patterns = [
        r'UNIDADE\s*/\s*SETOR:\s*([^/\n]+?)(?:/|$)',
        r'UNIDADE\s*SETOR:\s*([^/\n]+?)(?:/|RESPONSÁVEL TÉCNICO|$)',
        r'SETOR:\s*CE[T|P][^/\n]+'
    ]
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

def clean_cell_value(value):
    if not value: return ""
    # Tratar células mescladas mais agressivamente
    cleaned = str(value)
    if '\n' in cleaned:
        # Tentar extrair a primeira linha válida
        lines = cleaned.split('\n')
        for line in lines:
            if line.strip() and len(line.strip()) > 2:
                cleaned = line.strip()
                break
    return ' '.join(cleaned.replace('\n', ' ').split())

def extrair_todas_tabelas(doc_page):
    """NOVA FUNÇÃO: Extrai todas as tabelas usando múltiplas estratégias"""
    todas_rows = []
    
    # Estratégia 1: find_tables padrão
    try:
        for table in doc_page.find_tables():
            if table.extract():
                todas_rows.extend(table.extract())
    except:
        pass
    
    # Estratégia 2: find_tables com configurações diferentes
    try:
        for table in doc_page.find_tables(strategy="explicit"):
            if table.extract():
                todas_rows.extend(table.extract())
    except:
        pass
    
    # Estratégia 3: Tentar com configurações mais permissivas
    try:
        for table in doc_page.find_tables(strategy="lines"):
            if table.extract():
                todas_rows.extend(table.extract())
    except:
        pass
    
    return todas_rows

def is_valid_professional_name(name):
    """CORREÇÃO MAJOR: Validação muito mais permissiva"""
    if not name or not isinstance(name, str):
        return False
    
    name_clean = name.upper().strip()
    
    # Filtros básicos obrigatórios
    if len(name_clean) < 3:
        return False
    
    # Palavras que definitivamente NÃO são nomes
    definite_invalid = [
        'NOME COMPLETO', 'CARGO', 'MATRÍCULA', 'HORÁRIO', 'LEGENDA', 
        'MÊS/ANO', 'GOVERNO', 'SECRETARIA', 'DOCUMENTO', 'ASSINADO',
        'ÚLTIMA ALTERAÇÃO', 'ESCALA DE PLANTÃO', 'UNIDADE', 'SETOR'
    ]
    
    for invalid in definite_invalid:
        if invalid in name_clean:
            return False
    
    # Se é só números, não é nome
    if name_clean.replace('.', '').replace('-', '').replace(' ', '').isdigit():
        return False
    
    # MUDANÇA MAJOR: Aceitar até nomes com 1 palavra se tiver letras suficientes
    palavras = [p for p in name_clean.split() if any(c.isalpha() for c in p)]
    
    # Aceitar se tem pelo menos 1 palavra com 3+ caracteres OU 2+ palavras
    if len(palavras) >= 1:
        palavra_principal = max(palavras, key=len) if palavras else ""
        if len(palavra_principal) >= 3:
            return True
    
    return len(palavras) >= 2

def encontrar_todas_colunas_nomes(rows):
    """NOVA FUNÇÃO: Encontra todas as colunas que podem conter nomes"""
    colunas_nomes = set()
    
    for i, row in enumerate(rows[:20]):  # Verifica primeiras 20 linhas
        if not row:
            continue
            
        for j, cell in enumerate(row):
            if is_valid_professional_name(cell):
                colunas_nomes.add(j)
    
    return list(colunas_nomes)

def extrair_profissionais_agressivo(rows, mes, ano):
    """NOVA FUNÇÃO: Extração muito mais agressiva"""
    profissionais = []
    
    # Encontrar todas as possíveis colunas de nomes
    colunas_nomes = encontrar_todas_colunas_nomes(rows)
    
    print(f"DEBUG: Encontradas {len(colunas_nomes)} colunas com nomes: {colunas_nomes}")
    
    # Extrair de cada coluna encontrada
    for nome_col in colunas_nomes:
        for i, row in enumerate(rows):
            if not row or nome_col >= len(row):
                continue
                
            nome_candidato = clean_cell_value(row[nome_col])
            
            if is_valid_professional_name(nome_candidato):
                # Extrair informações adicionais da mesma linha
                cargo = ""
                vinculo = ""
                crm = ""
                
                # Tentar pegar informações das colunas seguintes
                if nome_col + 1 < len(row) and row[nome_col + 1]:
                    cargo = clean_cell_value(row[nome_col + 1])
                if nome_col + 2 < len(row) and row[nome_col + 2]:
                    next_field = clean_cell_value(row[nome_col + 2])
                    # Se parece com matrícula ou vínculo
                    if any(char.isdigit() for char in next_field) or 'PJ' in next_field.upper():
                        vinculo = next_field
                    else:
                        cargo = next_field if not cargo else cargo
                
                # Tentar encontrar CRM na linha
                for cell in row:
                    if cell and isinstance(cell, str):
                        cell_clean = clean_cell_value(cell)
                        if cell_clean.isdigit() and len(cell_clean) >= 3:
                            crm = cell_clean
                            break
                
                # Extrair plantões desta linha
                plantoes_raw = defaultdict(list)
                
                # Verificar cada célula da linha para plantões
                for j, cell in enumerate(row):
                    if j <= nome_col + 3:  # Pular colunas de dados pessoais
                        continue
                        
                    token = clean_cell_value(cell)
                    if token and len(token) <= 5:  # Tokens de plantão são curtos
                        # Tentar descobrir qual dia corresponde
                        dia_provavel = j - nome_col - 3  # Estimativa
                        if 1 <= dia_provavel <= 31:
                            plantoes_raw[dia_provavel].append(token)
                
                # Converter plantões
                plantoes_final = []
                for dia, tokens in plantoes_raw.items():
                    for token in set(tokens):
                        turnos_interpretados = interpretar_turno(token)
                        for turno_info in turnos_interpretados:
                            horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                            try:
                                data_plantao = datetime(ano, mes, dia)
                                if turno_info["turno"] == "NOITE (fim)": 
                                    data_plantao += timedelta(days=1)
                                plantoes_final.append({
                                    "dia": data_plantao.day,
                                    "data": data_plantao.strftime('%d/%m/%Y'),
                                    "turno": turno_info["turno"],
                                    "inicio": horarios.get("inicio"),
                                    "fim": horarios.get("fim")
                                })
                            except ValueError:
                                continue
                
                # Criar profissional
                professional = {
                    "medico_nome": nome_candidato,
                    "medico_crm": crm or "N/I",
                    "medico_especialidade": cargo or "N/I",
                    "medico_vinculo": vinculo or "N/I",
                    "plantoes": sorted(plantoes_final, key=lambda p: (
                        datetime.strptime(p["data"], '%d/%m/%Y'), 
                        p.get("inicio", "")
                    ))
                }
                
                profissionais.append(professional)
    
    print(f"DEBUG: Extraídos {len(profissionais)} profissionais")
    return profissionais

def interpretar_turno(token):
    turnos = []
    if not token: return turnos
    token_clean = token.upper().replace('/', '').replace(' ', '')
    if 'M' in token_clean: turnos.append({"turno": "MANHÃ"})
    if 'T' in token_clean: turnos.append({"turno": "TARDE"})
    if 'D' in token_clean:
        turnos.append({"turno": "MANHÃ"}); turnos.append({"turno": "TARDE"})
    if 'N' in token_clean:
        turnos.append({"turno": "NOITE (início)"}); turnos.append({"turno": "NOITE (fim)"})
    unique_turnos = []
    seen_turno_names = set()
    for t in turnos:
        if t['turno'] not in seen_turno_names:
            unique_turnos.append(t)
            seen_turno_names.add(t['turno'])
    return unique_turnos

# --- ENDPOINT PRINCIPAL ---
@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        global_unidade, global_setor, global_mes, global_ano = None, None, None, None
        pages_content = []

        print("DEBUG: Iniciando processamento...")

        # --- PASSADA 1: Coleta de dados globais ---
        for idx, page_data in enumerate(body):
            pdf_bytes = base64.b64decode(page_data.get("bae64"))
            
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                
                print(f"DEBUG: Processando página {idx + 1}")
                
                # Extrair dados globais
                unidade, setor = extract_unidade_setor_from_text(page_text)
                mes, ano = parse_mes_ano(page_text)
                
                if unidade: global_unidade = unidade
                if setor: global_setor = setor
                if mes: global_mes = mes
                if ano: global_ano = ano
                
                # USAR EXTRAÇÃO AGRESSIVA DE TABELAS
                page_rows = extrair_todas_tabelas(page)
                
                print(f"DEBUG: Página {idx + 1} - {len(page_rows)} linhas extraídas")
                
            pages_content.append({
                "rows": page_rows, 
                "setor_pagina": setor,
                "page_num": idx + 1
            })

        if not global_mes or not global_ano:
            return JSONResponse(
                content={"error": "Não foi possível determinar o Mês/Ano da escala."}, 
                status_code=400
            )

        print(f"DEBUG: Dados globais - Unidade: {global_unidade}, Setor: {global_setor}, Mês/Ano: {global_mes}/{global_ano}")

        # --- PASSADA 2: Extração agressiva ---
        all_professionals_map = {}
        
        for page in pages_content:
            all_rows = page["rows"]
            setor_a_usar = page["setor_pagina"] or global_setor or "NÃO INFORMADO"
            
            print(f"DEBUG: Processando página {page['page_num']} com {len(all_rows)} linhas")
            
            # USAR EXTRAÇÃO AGRESSIVA
            profissionais = extrair_profissionais_agressivo(all_rows, global_mes, global_ano)
            
            print(f"DEBUG: Página {page['page_num']} - {len(profissionais)} profissionais encontrados")
            
            # Consolidar profissionais
            for professional in profissionais:
                nome_key = professional["medico_nome"]
                
                if nome_key not in all_professionals_map:
                    professional["medico_setor"] = setor_a_usar
                    all_professionals_map[nome_key] = professional
                else:
                    # Mesclar dados
                    existing = all_professionals_map[nome_key]
                    new_plantoes = professional["plantoes"]
                    
                    # Adicionar plantões únicos
                    for plantao in new_plantoes:
                        if plantao not in existing["plantoes"]:
                            existing["plantoes"].append(plantao)
                    
                    # Reordenar
                    existing["plantoes"].sort(
                        key=lambda p: (datetime.strptime(p["data"], '%d/%m/%Y'), p.get("inicio", ""))
                    )
                    
                    # Atualizar campos se vazios
                    if professional["medico_crm"] != "N/I" and existing["medico_crm"] == "N/I":
                        existing["medico_crm"] = professional["medico_crm"]
                    if professional["medico_especialidade"] != "N/I" and existing["medico_especialidade"] == "N/I":
                        existing["medico_especialidade"] = professional["medico_especialidade"]
                    if professional["medico_vinculo"] != "N/I" and existing["medico_vinculo"] == "N/I":
                        existing["medico_vinculo"] = professional["medico_vinculo"]

        print(f"DEBUG: Total final - {len(all_professionals_map)} profissionais únicos")

        # Gerar resposta
        mes_nome = [k for k, v in MONTH_MAP.items() if v == global_mes][0]
        result = [{
            "unidade_escala": global_unidade or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome}/{global_ano}",
            "profissionais": list(all_professionals_map.values())
        }]
        
        return JSONResponse(content=result)
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return JSONResponse(
            content={"error": str(e), "trace": traceback.format_exc()}, 
            status_code=500
        )
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
