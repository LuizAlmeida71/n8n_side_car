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
import hashlib

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

# Base de padrões conhecidos
PADROES_CONHECIDOS = {}

# --- CLASSES PARA PADRÕES ---
class EscalaPattern:
    def __init__(self, tipo, assinatura, caracteristicas):
        self.tipo = tipo
        self.assinatura = assinatura
        self.caracteristicas = caracteristicas
        self.extrator = self._get_extrator()
    
    def _get_extrator(self):
        """Retorna função de extração específica para o padrão"""
        if self.tipo == "TIPO_1":
            return self._extrair_tipo_1
        elif self.tipo == "TIPO_2":
            return self._extrair_tipo_2
        elif self.tipo == "TIPO_3":
            return self._extrair_tipo_3
        elif self.tipo == "TIPO_4":
            return self._extrair_tipo_4
        return self._extrair_generico
    
    def _extrair_tipo_1(self, rows, header_map, nome_idx):
        # Extração para tipo sem mesclagens, unidade textual
        return self._extrair_generico(rows, header_map, nome_idx)
    
    def _extrair_tipo_2(self, rows, header_map, nome_idx):
        # Extração para 3 níveis sem merge
        return self._extrair_generico(rows, header_map, nome_idx)
    
    def _extrair_tipo_3(self, rows, header_map, nome_idx):
        # Extração para 3 níveis com merge - precisa desfazer mesclagens
        rows_processadas = self._desfazer_mesclagens(rows)
        return self._extrair_generico(rows_processadas, header_map, nome_idx)
    
    def _extrair_tipo_4(self, rows, header_map, nome_idx):
        # Extração com coluna numérica
        return self._extrair_generico(rows, header_map, nome_idx)
    
    def _extrair_generico(self, rows, header_map, nome_idx, mes=None, ano=None):
        # Implementação genérica melhorada
        return extrair_profissionais_melhorado(rows, header_map, nome_idx, mes, ano)
    
    def _desfazer_mesclagens(self, rows):
        """Desfaz mesclagens identificadas nas células"""
        rows_processadas = []
        for row in rows:
            row_processada = []
            for cell in row:
                if cell and '\n' in str(cell):
                    # Célula mesclada - redistribuir conteúdo
                    parts = str(cell).split('\n')
                    row_processada.append(parts[0])  # Primeira parte na célula atual
                    # Armazenar outras partes para redistribuição
                else:
                    row_processada.append(cell)
            rows_processadas.append(row_processada)
        return rows_processadas

# --- FUNÇÕES DE DETECÇÃO E CLASSIFICAÇÃO ---

def extrair_elementos_estruturais(page_text, rows):
    """Extrai elementos estruturais da página"""
    elementos = {}
    
    # Detectar logo do governo
    elementos['tem_logo_governo'] = 'Governo do Estado' in page_text or 'Secretaria de Estado' in page_text
    
    # Extrair unidade e setor
    elementos['unidade'], elementos['setor'] = extract_unidade_setor_from_text(page_text)
    
    # Extrair mês/ano
    elementos['mes'], elementos['ano'] = parse_mes_ano(page_text)
    
    # Analisar estrutura da tabela
    elementos['niveis_cabecalho'] = contar_niveis_cabecalho(rows)
    elementos['tem_coluna_numerica'] = detectar_coluna_numerica(rows)
    elementos['tem_mesclagens'] = detectar_mesclagens(rows)
    elementos['tipo_layout'] = determinar_tipo_layout(page_text, elementos)
    
    return elementos

def contar_niveis_cabecalho(rows):
    """Conta níveis de cabeçalho na tabela"""
    niveis = 0
    for row in rows[:10]:  # Verifica primeiras 10 linhas
        if is_header_row_melhorado(row):
            niveis += 1
        elif niveis > 0:  # Parou de encontrar cabeçalhos
            break
    return niveis

def detectar_coluna_numerica(rows):
    """Detecta se há coluna numérica no início"""
    if not rows:
        return False
    
    for row in rows[:5]:  # Verifica primeiras linhas
        if row and row[0]:
            primeiro_cell = str(row[0]).strip()
            if primeiro_cell.isdigit() or primeiro_cell in ['#', 'Nº']:
                return True
    return False

def detectar_mesclagens(rows):
    """Detecta se há células mescladas"""
    for row in rows:
        for cell in row:
            if cell and '\n' in str(cell) and len(str(cell).split('\n')) > 1:
                return True
    return False

def determinar_tipo_layout(page_text, elementos):
    """Determina o tipo de layout baseado nas características"""
    if not elementos['tem_logo_governo']:
        return "TIPO_CONTINUACAO"
    
    if elementos['unidade'] and 'CENTRAL ESTADUAL' in page_text:
        return "TIPO_1"  # Unidade textual
    elif elementos['niveis_cabecalho'] == 3:
        if elementos['tem_mesclagens']:
            return "TIPO_3"  # 3 níveis com merge
        else:
            return "TIPO_2"  # 3 níveis sem merge
    elif elementos['tem_coluna_numerica']:
        return "TIPO_4"  # Com coluna numérica
    
    return "TIPO_GENERICO"

def calcular_assinatura(elementos):
    """Calcula assinatura única do padrão"""
    signature_data = f"{elementos['tipo_layout']}_{elementos['niveis_cabecalho']}_{elementos['tem_coluna_numerica']}_{elementos['tem_mesclagens']}"
    return hashlib.md5(signature_data.encode()).hexdigest()

def detectar_continuacao(elementos):
    """Detecta se é página de continuação"""
    return elementos['tipo_layout'] == "TIPO_CONTINUACAO" or (
        not elementos['tem_logo_governo'] and 
        not elementos['unidade'] and 
        not elementos['setor']
    )

# --- FUNÇÕES MELHORADAS DE EXTRAÇÃO ---

def is_header_row_melhorado(row):
    """Versão melhorada da detecção de cabeçalho"""
    if not row or len(row) < 2:
        return False
    
    row_text = ' '.join([str(cell) for cell in row if cell]).upper()
    
    # Indicadores primários de cabeçalho
    header_indicators = [
        'NOME COMPLETO', 'NOME', 'CARGO', 'MATRÍCULA', 'VÍNCULO', 
        'CONSELHO', 'HORÁRIO', 'C.H', 'CRM'
    ]
    
    indicator_count = sum(1 for indicator in header_indicators if indicator in row_text)
    
    # Indicadores de dias do mês
    day_count = 0
    consecutive_days = 0
    for cell in row:
        if cell and str(cell).strip().isdigit():
            day = int(str(cell).strip())
            if 1 <= day <= 31:
                day_count += 1
                consecutive_days += 1
            else:
                consecutive_days = 0
        else:
            consecutive_days = 0
    
    # É cabeçalho se tem indicadores de campos OU muitos dias consecutivos
    return indicator_count >= 1 or (day_count >= 5 and consecutive_days >= 3)

def build_header_map_melhorado(row):
    """Versão melhorada do mapeamento de cabeçalho"""
    header_map = {}
    nome_idx = None
    
    # Detectar se tem coluna numérica no início
    start_col = 0
    if row and row[0] and (str(row[0]).strip().isdigit() or str(row[0]).strip() in ['#', 'Nº']):
        start_col = 1
    
    for i, cell in enumerate(row):
        if not cell:
            continue
            
        cell_text = str(cell).replace('\n', ' ').strip().upper()
        
        # Mapeamento mais flexível
        if any(termo in cell_text for termo in ['NOME', 'COMPLETO']):
            nome_idx = header_map["NOME COMPLETO"] = i
        elif 'CARGO' in cell_text:
            header_map["CARGO"] = i
        elif any(termo in cell_text for termo in ['VÍNCULO', 'VINCULO']):
            header_map["VÍNCULO"] = i
        elif any(termo in cell_text for termo in ['CRM', 'CONSELHO']):
            header_map["CRM"] = i
        elif 'MATRÍCULA' in cell_text:
            header_map["MATRÍCULA"] = i
        elif 'HORÁRIO' in cell_text:
            header_map["HORÁRIO"] = i
        elif cell_text in ['CH', 'C.H', 'C.H.']:
            header_map["CH"] = i
        else:
            # Tentar interpretar como dia
            try:
                day = int(cell_text)
                if 1 <= day <= 31:
                    header_map[day] = i
            except (ValueError, TypeError):
                pass
    
    return header_map, nome_idx

def is_valid_professional_name_melhorado(name):
    """Versão melhorada da validação de nomes"""
    if not name or not isinstance(name, str) or len(name.strip()) < 3:
        return False
    
    name_clean = name.upper().strip()
    
    # Filtros mais específicos
    invalid_keywords = [
        'NOME COMPLETO', 'CARGO', 'MATRÍCULA', 'HORÁRIO', 'LEGENDA', 
        'ASSINATURA', 'UNIDADE', 'SETOR', 'MÊS', 'ANO', 'ALTERAÇÃO', 
        'GOVERNO', 'SECRETARIA', 'DOCUMENTO', 'PÁGINA'
    ]
    
    if any(keyword in name_clean for keyword in invalid_keywords):
        return False
    
    # Não pode ser só números
    if name_clean.replace('.', '').replace('-', '').replace(' ', '').isdigit():
        return False
    
    # Deve ter pelo menos 2 palavras para ser nome completo
    palavras = name_clean.split()
    if len(palavras) < 2:
        return False
    
    # Verificar se tem padrão de nome (letras principalmente)
    letra_count = sum(1 for c in name_clean if c.isalpha())
    if letra_count < len(name_clean) * 0.7:  # Pelo menos 70% letras
        return False
    
    return True

def extrair_profissionais_melhorado(rows, header_map, nome_idx, mes=None, ano=None):
    """Extração melhorada de profissionais"""
    profissionais = []
    i = 0
    
    while i < len(rows):
        row = rows[i]
        
        if not header_map or nome_idx is None or nome_idx >= len(row):
            i += 1
            continue
        
        nome_raw = row[nome_idx] if nome_idx < len(row) else None
        
        if is_valid_professional_name_melhorado(nome_raw):
            professional, next_i = process_professional_shifts_melhorado(
                rows, i, header_map, nome_idx, mes, ano
            )
            if professional:
                profissionais.append(professional)
            i = next_i
        else:
            i += 1
    
    return profissionais

def process_professional_shifts_melhorado(rows, start_idx, header_map, nome_idx, mes=None, ano=None):
    """Versão melhorada do processamento de plantões"""
    current_row = rows[start_idx]
    nome = clean_cell_value(current_row[nome_idx])
    
    # Extrair informações básicas
    info = {}
    for key, idx in header_map.items():
        if isinstance(key, str) and idx < len(current_row):
            info[key] = clean_cell_value(current_row[idx])
    
    professional = {
        "medico_nome": nome,
        "medico_crm": info.get("CRM", "N/I"),
        "medico_especialidade": info.get("CARGO", "N/I"),
        "medico_vinculo": info.get("VÍNCULO", "N/I"),
        "plantoes_raw": defaultdict(list)
    }
    
    # Processar todas as linhas do profissional
    idx = start_idx
    while idx < len(rows):
        row_to_process = rows[idx]
        
        # Verificar se chegou no próximo profissional
        if idx > start_idx:
            next_name = clean_cell_value(row_to_process[nome_idx]) if nome_idx < len(row_to_process) else None
            if next_name and is_valid_professional_name_melhorado(next_name):
                break
        
        # Extrair tokens de plantão para cada dia
        for dia, col_idx in header_map.items():
            if isinstance(dia, int) and col_idx < len(row_to_process) and row_to_process[col_idx]:
                token = clean_cell_value(row_to_process[col_idx])
                if token and token not in ['', '-', '—']:
                    professional["plantoes_raw"][dia].append(token)
        
        idx += 1
    
    # Converter plantões para formato final
    professional["plantoes"] = converter_plantoes_melhorado(
        professional["plantoes_raw"], mes, ano
    )
    del professional["plantoes_raw"]
    
    return professional, idx

def converter_plantoes_melhorado(plantoes_raw, mes=None, ano=None):
    """Converte plantões raw para formato estruturado"""
    if not mes or not ano:
        mes, ano = 5, 2025  # Fallback
        
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
    
    # Remover duplicatas e ordenar
    seen = set()
    plantoes_dedup = [p for p in plantoes_final 
                     if tuple(p.items()) not in seen and not seen.add(tuple(p.items()))]
    
    return sorted(plantoes_dedup, key=lambda p: (
        datetime.strptime(p["data"], '%d/%m/%Y'), 
        p.get("inicio", "")
    ))

# --- FUNÇÕES AUXILIARES EXISTENTES ---
def parse_mes_ano(text):
    """Função existente mantida"""
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
    """Função existente mantida"""
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
    """Função existente mantida"""
    if not value:
        return ""
    return ' '.join(str(value).replace('\n', ' ').split())

def interpretar_turno(token):
    """Função existente mantida"""
    turnos = []
    if not token:
        return turnos
    token_clean = token.upper().replace('/', '').replace(' ', '')
    if 'M' in token_clean:
        turnos.append({"turno": "MANHÃ"})
    if 'T' in token_clean:
        turnos.append({"turno": "TARDE"})
    if 'D' in token_clean:
        turnos.append({"turno": "MANHÃ"})
        turnos.append({"turno": "TARDE"})
    if 'N' in token_clean:
        turnos.append({"turno": "NOITE (início)"})
        turnos.append({"turno": "NOITE (fim)"})
    unique_turnos = []
    seen_turno_names = set()
    for t in turnos:
        if t['turno'] not in seen_turno_names:
            unique_turnos.append(t)
            seen_turno_names.add(t['turno'])
    return unique_turnos

# --- PROCESSADOR PRINCIPAL MELHORADO ---

def processar_pagina_com_padrao(page_content, elementos, padrao_anterior=None):
    """Processa página usando detecção de padrões"""
    
    try:
        # Verificar se é continuação
        if detectar_continuacao(elementos) and padrao_anterior:
            if hasattr(padrao_anterior, 'header_map') and hasattr(padrao_anterior, 'nome_idx'):
                return padrao_anterior.extrator(
                    page_content["rows"], 
                    padrao_anterior.header_map, 
                    padrao_anterior.nome_idx
                )
            else:
                # Fallback se padrão anterior não tem dados suficientes
                return extrair_profissionais_melhorado(
                    page_content["rows"], {}, None
                )
        
        # Calcular assinatura do padrão
        assinatura = calcular_assinatura(elementos)
        
        # Verificar se padrão já é conhecido
        if assinatura in PADROES_CONHECIDOS:
            padrao = PADROES_CONHECIDOS[assinatura]
        else:
            # Criar novo padrão
            padrao = EscalaPattern(
                tipo=elementos['tipo_layout'],
                assinatura=assinatura,
                caracteristicas=elementos
            )
            PADROES_CONHECIDOS[assinatura] = padrao
        
        # Encontrar cabeçalho
        header_map, nome_idx = None, None
        for row in page_content["rows"]:
            if is_header_row_melhorado(row):
                header_map, nome_idx = build_header_map_melhorado(row)
                break
        
        if not header_map or nome_idx is None:
            # Tentar extração genérica se não encontrar cabeçalho
            return extrair_sem_cabecalho(page_content["rows"])
        
        # Armazenar no padrão para páginas de continuação
        padrao.header_map = header_map
        padrao.nome_idx = nome_idx
        
        # Extrair profissionais usando o padrão
        mes_global = elementos.get("mes_global", elementos.get("mes", 5))
        ano_global = elementos.get("ano_global", elementos.get("ano", 2025))
        
        profissionais = padrao.extrator(page_content["rows"], header_map, nome_idx)
        
        return profissionais
        
    except Exception as e:
        print(f"Erro no processamento de padrão: {e}")
        # Fallback para extração básica
        return extrair_profissionais_basico(page_content["rows"])

def extrair_sem_cabecalho(rows):
    """Extração para páginas sem cabeçalho identificável"""
    profissionais = []
    
    for row in rows:
        if row and len(row) > 0:
            primeiro_campo = clean_cell_value(row[0])
            if is_valid_professional_name_melhorado(primeiro_campo):
                # Assumir estrutura básica: nome na primeira coluna
                professional = {
                    "medico_nome": primeiro_campo,
                    "medico_crm": "N/I",
                    "medico_especialidade": "N/I", 
                    "medico_vinculo": "N/I",
                    "plantoes": []
                }
                profissionais.append(professional)
    
    return profissionais

def extrair_profissionais_basico(rows):
    """Extração básica como fallback"""
    profissionais = []
    
    for i, row in enumerate(rows):
        if not row:
            continue
            
        # Tentar encontrar nomes em qualquer posição
        for j, cell in enumerate(row):
            if is_valid_professional_name_melhorado(cell):
                professional = {
                    "medico_nome": clean_cell_value(cell),
                    "medico_crm": "N/I",
                    "medico_especialidade": "N/I",
                    "medico_vinculo": "N/I", 
                    "plantoes": []
                }
                
                # Tentar extrair outros campos da mesma linha
                if j + 1 < len(row) and row[j + 1]:
                    professional["medico_especialidade"] = clean_cell_value(row[j + 1])
                
                profissionais.append(professional)
                break
    
    return profissionais

# --- ENDPOINT PRINCIPAL MELHORADO ---

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf_melhorado(request: Request):
    try:
        body = await request.json()
        global_unidade, global_setor, global_mes, global_ano = None, None, None, None
        pages_content = []
        padrao_atual = None

        # --- PASSADA 1: Análise estrutural e coleta de dados ---
        for page_data in body:
            pdf_bytes = base64.b64decode(page_data.get("bae64"))
            page_rows = []
            
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                
                # Extrair elementos estruturais
                elementos = extrair_elementos_estruturais(page_text, [])
                
                # Atualizar dados globais se encontrados
                if elementos['unidade']:
                    global_unidade = elementos['unidade']
                if elementos['setor']:
                    global_setor = elementos['setor']
                if elementos['mes']:
                    global_mes = elementos['mes']
                if elementos['ano']:
                    global_ano = elementos['ano']
                
                # Extrair tabelas
                for table in page.find_tables():
                    if table.extract():
                        page_rows.extend(table.extract())
                
                # Completar análise estrutural com dados da tabela
                elementos_completos = extrair_elementos_estruturais(page_text, page_rows)
                
                pages_content.append({
                    "rows": page_rows,
                    "elementos": elementos_completos,
                    "setor_pagina": elementos['setor']
                })

        if not global_mes or not global_ano:
            return JSONResponse(
                content={"error": "Não foi possível determinar o Mês/Ano da escala."}, 
                status_code=400
            )

        # --- PASSADA 2: Processamento com padrões ---
        all_professionals_map = {}
        
        for page in pages_content:
            setor_a_usar = page["setor_pagina"] or global_setor or "NÃO INFORMADO"
            
            # Processar página com detecção de padrões (passando mes e ano globais)
            page["elementos"]["mes_global"] = global_mes
            page["elementos"]["ano_global"] = global_ano
            
            profissionais = processar_pagina_com_padrao(
                page, 
                page["elementos"], 
                padrao_atual
            )
            
            # Atualizar padrão atual se não for continuação
            if not detectar_continuacao(page["elementos"]):
                assinatura = calcular_assinatura(page["elementos"])
                padrao_atual = PADROES_CONHECIDOS.get(assinatura)
            
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
                    
                    # Adicionar novos plantões evitando duplicatas
                    existing["plantoes"].extend(
                        p for p in new_plantoes if p not in existing["plantoes"]
                    )
                    
                    # Reordenar cronologicamente
                    existing["plantoes"].sort(
                        key=lambda p: (
                            datetime.strptime(p["data"], '%d/%m/%Y'), 
                            p.get("inicio", "")
                        )
                    )
                    
                    # Atualizar setor se necessário
                    if (setor_a_usar != "NÃO INFORMADO" and 
                        existing["medico_setor"] == "NÃO INFORMADO"):
                        existing["medico_setor"] = setor_a_usar

        # Gerar resposta
        mes_nome = [k for k, v in MONTH_MAP.items() if v == global_mes][0]
        result = [{
            "unidade_escala": global_unidade or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome}/{global_ano}",
            "profissionais": list(all_professionals_map.values())
        }]
        
        return JSONResponse(content=result)
        
    except Exception as e:
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
