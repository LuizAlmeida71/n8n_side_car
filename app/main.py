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
import re
import base64
import fitz
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import Request
from fastapi.responses import JSONResponse
import traceback
from typing import Dict, List, Tuple, Optional

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

# --- CLASSE PARA GERENCIAR CONTEXTO ENTRE PÁGINAS ---
class ContextoEscala:
    def __init__(self):
        self.header_map = None
        self.nome_idx = None
        self.tem_coluna_numerica = False
        self.tipo_escala = None
        self.ultimo_profissional = None
        
    def atualizar(self, header_map, nome_idx, tem_coluna_numerica=False):
        self.header_map = header_map
        self.nome_idx = nome_idx
        self.tem_coluna_numerica = tem_coluna_numerica
        
    def eh_valido(self):
        return self.header_map is not None and self.nome_idx is not None

# --- FUNÇÕES AUXILIARES MELHORADAS ---

def detectar_tipo_escala(page_text: str) -> str:
    """Detecta o tipo de escala baseado em palavras-chave"""
    text_upper = page_text.upper()
    
    if "CENTRAL ESTADUAL DE TRANSPLANTE" in text_upper:
        return "TIPO_A"  # Cabeçalho fora da tabela
    elif "UNIDADE DE TERAPIA INTENSIVA" in text_upper:
        return "TIPO_C"  # Com mesclagem e coluna numérica
    elif "RESPONSÁVEL TÉCNICO" in text_upper:
        return "TIPO_D"  # Sem mesclagem mas com coluna numérica
    elif "ESCALA DE PLANTÃO" in text_upper:
        return "TIPO_B"  # Padrão sem coluna numérica
    else:
        return "TIPO_E"  # Continuação

def parse_mes_ano(text):
    """Função mais robusta para extrair mês e ano"""
    patterns = [
        r'MÊS/ANO:\s*([A-ZÇÃ]+)\s*/\s*(\d{4})',
        r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})',
        r'MÊS:\s*([A-ZÇÃ]+)\s*/\s*(\d{4})',
        r'(?:MÊS:\s*|MÊS\s+)([A-ZÇÃ]+)\s+(\d{4})',
        r'([A-ZÇÃ]+)\s*/\s*(\d{4})'  # Padrão mais genérico
    ]
    
    text_upper = text.upper()
    
    for pattern in patterns:
        matches = re.finditer(pattern, text_upper)
        for match in matches:
            mes_nome, ano_str = match.groups()
            mes = MONTH_MAP.get(mes_nome.strip())
            if mes:
                try:
                    ano = int(ano_str)
                    if 2020 <= ano <= 2030:  # Validação de ano razoável
                        return mes, ano
                except (ValueError, TypeError):
                    pass
    return None, None

def is_header_row_improved(row, contexto: ContextoEscala) -> bool:
    """Detecção de cabeçalho melhorada com contexto"""
    if not row or len(row) < 3:
        return False
    
    # Se é continuação e já temos contexto válido, não é cabeçalho
    if contexto.eh_valido() and contexto.tipo_escala == "TIPO_E":
        return False
    
    row_text = ' '.join([str(cell) for cell in row if cell]).upper()
    
    # Indicadores fortes de cabeçalho
    header_indicators = ['NOME', 'CARGO', 'MATRÍCULA', 'VÍNCULO', 'CONSELHO', 
                        'HORÁRIO', 'C.H', 'CRM']
    
    # Conta indicadores
    indicator_count = sum(1 for indicator in header_indicators if indicator in row_text)
    
    # Conta possíveis dias
    day_count = 0
    for cell in row:
        cell_str = str(cell).strip()
        if cell_str.isdigit():
            try:
                day = int(cell_str)
                if 1 <= day <= 31:
                    day_count += 1
            except:
                pass
    
    # É cabeçalho se tem pelo menos 2 indicadores OU mais de 10 dias
    return indicator_count >= 2 or day_count >= 10

def build_header_map_improved(row, tipo_escala: str) -> Tuple[Dict, Optional[int], bool]:
    """Construção de mapa de cabeçalho melhorada por tipo"""
    header_map = {}
    nome_idx = None
    tem_coluna_numerica = False
    
    # Detecta se primeira coluna é numérica
    if row and row[0] and str(row[0]).strip().isdigit():
        tem_coluna_numerica = True
        start_col = 1
    else:
        start_col = 0
    
    # Para tipo C e D, esperamos coluna numérica
    if tipo_escala in ["TIPO_C", "TIPO_D"] and not tem_coluna_numerica:
        # Força detecção de coluna numérica
        if row and len(row) > 1:
            # Verifica se segunda célula parece ser nome
            if row[0] and not any(ind in str(row[0]).upper() for ind in ['NOME', 'CARGO']):
                tem_coluna_numerica = True
                start_col = 1
    
    # Mapeia as colunas
    for i, cell in enumerate(row[start_col:], start=start_col):
        if not cell:
            continue
            
        cell_text = str(cell).replace('\n', ' ').strip().upper()
        
        # Mapeamento de campos
        if 'NOME' in cell_text and nome_idx is None:
            nome_idx = i
            header_map["NOME COMPLETO"] = i
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
                day = int(cell_text)
                if 1 <= day <= 31:
                    header_map[day] = i
            except (ValueError, TypeError):
                # Pode ser dia da semana
                if cell_text in ['S', 'D', 'Q', 'T', 'SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB', 'DOM']:
                    pass  # Ignora dias da semana
    
    return header_map, nome_idx, tem_coluna_numerica

def is_valid_professional_name_improved(name: str) -> bool:
    """Validação de nome melhorada"""
    if not name or not isinstance(name, str):
        return False
    
    name_clean = name.strip()
    if len(name_clean) < 3:
        return False
    
    name_upper = name_clean.upper()
    
    # Lista expandida de palavras inválidas
    invalid_keywords = [
        'NOME COMPLETO', 'CARGO', 'MATRÍCULA', 'HORÁRIO', 'LEGENDA', 
        'ASSINATURA', 'UNIDADE', 'SETOR', 'MÊS', 'ANO', 'ALTERAÇÃO', 
        'GOVERNO', 'SECRETARIA', 'ESCALA DE PLANTÃO', 'DOCUMENTO',
        'CARGA HORARIA', 'PRODUTIVIDADE', 'DIA/NOITE', 'MANHÃ',
        'TARDE', 'NOITE', 'TOTAL', 'CONSELHO', 'CLASSE',
        'AUTENTICIDADE', 'VERIFICADOR', 'CÓDIGO'
    ]
    
    if any(keyword in name_upper for keyword in invalid_keywords):
        return False
    
    # Não pode ser apenas números/pontuação
    if name_clean.replace('.', '').replace('-', '').replace(' ', '').replace('/', '').isdigit():
        return False
    
    # Deve ter pelo menos uma letra
    if not any(c.isalpha() for c in name_clean):
        return False
    
    # Aceita nomes com uma palavra se tiver mais de 4 caracteres
    palavras = name_clean.split()
    if len(palavras) == 1:
        return len(name_clean) > 4 and name_clean.isalpha()
    
    # Para múltiplas palavras, pelo menos 2
    return len(palavras) >= 2

def extract_professional_from_row(row: List, header_map: Dict, nome_idx: int, 
                                tem_coluna_numerica: bool) -> Optional[Dict]:
    """Extrai informações do profissional de uma linha"""
    if not row or nome_idx >= len(row):
        return None
    
    # Ajusta índice se tem coluna numérica
    actual_nome_idx = nome_idx
    if tem_coluna_numerica and row[0] and str(row[0]).strip().isdigit():
        # Se a primeira coluna é número, todos os índices já estão corretos
        pass
    
    nome = clean_cell_value(row[actual_nome_idx])
    if not is_valid_professional_name_improved(nome):
        return None
    
    # Extrai outras informações
    info = {}
    for key, idx in header_map.items():
        if isinstance(key, str) and idx < len(row):
            info[key] = clean_cell_value(row[idx])
    
    return {
        "nome": nome,
        "cargo": info.get("CARGO", "N/I"),
        "crm": info.get("CRM", "N/I"),
        "vinculo": info.get("VÍNCULO", "N/I"),
        "matricula": info.get("MATRÍCULA", "N/I"),
        "horario": info.get("HORÁRIO", "N/I"),
        "ch": info.get("CH", "N/I")
    }

def clean_cell_value(value):
    """Limpa valor da célula"""
    if not value:
        return ""
    return ' '.join(str(value).replace('\n', ' ').split())

def interpretar_turno(token):
    """Interpreta turnos do token"""
    turnos = []
    if not token:
        return turnos
    
    token_clean = token.upper().replace('/', '').replace(' ', '')
    
    # Remove prefixos comuns
    for prefix in ['PJ', 'CH', 'PSS', 'EH']:
        token_clean = token_clean.replace(prefix, '')
    
    # Identifica turnos
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
    
    # Remove duplicatas
    unique_turnos = []
    seen = set()
    for t in turnos:
        if t['turno'] not in seen:
            unique_turnos.append(t)
            seen.add(t['turno'])
    
    return unique_turnos

def process_all_pages_improved(pages_data: List[Dict], contexto: ContextoEscala) -> Tuple[Dict, Dict]:
    """Processa todas as páginas com contexto compartilhado"""
    all_professionals = {}
    global_info = {
        "unidade": None,
        "setor": None,
        "mes": None,
        "ano": None
    }
    
    # Primeira passada: coleta informações globais
    for page_data in pages_data:
        pdf_bytes = base64.b64decode(page_data.get("bae64", page_data.get("base64", "")))
        
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc[0]
            page_text = page.get_text("text")
            
            # Detecta tipo de escala
            tipo = detectar_tipo_escala(page_text)
            if contexto.tipo_escala is None:
                contexto.tipo_escala = tipo
            
            # Extrai informações globais
            mes, ano = parse_mes_ano(page_text)
            if mes and ano:
                global_info["mes"] = mes
                global_info["ano"] = ano
            
            unidade, setor = extract_unidade_setor_from_text(page_text)
            if unidade:
                global_info["unidade"] = unidade
            if setor:
                global_info["setor"] = setor
    
    # Segunda passada: processa dados
    for page_idx, page_data in enumerate(pages_data):
        pdf_bytes = base64.b64decode(page_data.get("bae64", page_data.get("base64", "")))
        
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc[0]
            
            # Extrai tabelas
            all_rows = []
            for table in page.find_tables():
                if table.extract():
                    all_rows.extend(table.extract())
            
            if not all_rows:
                continue
            
            # Processa linhas
            i = 0
            while i < len(all_rows):
                row = all_rows[i]
                
                # Verifica se é cabeçalho
                if is_header_row_improved(row, contexto):
                    header_map, nome_idx, tem_coluna_numerica = build_header_map_improved(
                        row, contexto.tipo_escala
                    )
                    
                    if header_map and nome_idx is not None:
                        contexto.atualizar(header_map, nome_idx, tem_coluna_numerica)
                    
                    i += 1
                    continue
                
                # Se não tem contexto válido, pula
                if not contexto.eh_valido():
                    i += 1
                    continue
                
                # Tenta extrair profissional
                prof_info = extract_professional_from_row(
                    row, contexto.header_map, contexto.nome_idx, 
                    contexto.tem_coluna_numerica
                )
                
                if prof_info:
                    nome_key = prof_info["nome"]
                    
                    if nome_key not in all_professionals:
                        all_professionals[nome_key] = {
                            "medico_nome": prof_info["nome"],
                            "medico_crm": prof_info["crm"],
                            "medico_especialidade": prof_info["cargo"],
                            "medico_vinculo": prof_info["vinculo"],
                            "medico_setor": global_info["setor"] or "NÃO INFORMADO",
                            "plantoes_raw": defaultdict(list)
                        }
                    
                    # Coleta plantões desta linha
                    for dia, col_idx in contexto.header_map.items():
                        if isinstance(dia, int) and col_idx < len(row) and row[col_idx]:
                            token = clean_cell_value(row[col_idx])
                            if token and token not in ['', '-']:
                                all_professionals[nome_key]["plantoes_raw"][dia].append(token)
                    
                    # Verifica próximas linhas para o mesmo profissional
                    j = i + 1
                    while j < len(all_rows):
                        next_row = all_rows[j]
                        
                        # Se encontrou outro profissional, para
                        next_prof = extract_professional_from_row(
                            next_row, contexto.header_map, contexto.nome_idx,
                            contexto.tem_coluna_numerica
                        )
                        if next_prof:
                            break
                        
                        # Se é cabeçalho, para
                        if is_header_row_improved(next_row, contexto):
                            break
                        
                        # Coleta plantões adicionais
                        for dia, col_idx in contexto.header_map.items():
                            if isinstance(dia, int) and col_idx < len(next_row) and next_row[col_idx]:
                                token = clean_cell_value(next_row[col_idx])
                                if token and token not in ['', '-']:
                                    all_professionals[nome_key]["plantoes_raw"][dia].append(token)
                        
                        j += 1
                    
                    i = j
                else:
                    i += 1
    
    return all_professionals, global_info

# --- ENDPOINT PRINCIPAL ---
@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        
        # Cria contexto compartilhado
        contexto = ContextoEscala()
        
        # Processa todas as páginas
        all_professionals, global_info = process_all_pages_improved(body, contexto)
        
        # Valida informações obrigatórias
        if not global_info["mes"] or not global_info["ano"]:
            return JSONResponse(
                content={"error": "Não foi possível determinar o Mês/Ano da escala"}, 
                status_code=400
            )
        
        # Processa plantões finais
        for nome, prof in all_professionals.items():
            plantoes_final = []
            
            for dia, tokens in prof["plantoes_raw"].items():
                for token in set(tokens):
                    turnos = interpretar_turno(token)
                    
                    for turno_info in turnos:
                        horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                        
                        try:
                            data_plantao = datetime(global_info["ano"], global_info["mes"], dia)
                            
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
            
            # Remove duplicatas e ordena
            seen = set()
            plantoes_dedup = [
                p for p in plantoes_final 
                if tuple(p.items()) not in seen and not seen.add(tuple(p.items()))
            ]
            
            prof["plantoes"] = sorted(
                plantoes_dedup,
                key=lambda p: (datetime.strptime(p["data"], '%d/%m/%Y'), p.get("inicio", ""))
            )
            
            del prof["plantoes_raw"]
        
        # Monta resultado final
        mes_nome = [k for k, v in MONTH_MAP.items() if v == global_info["mes"]][0]
        
        result = [{
            "unidade_escala": global_info["unidade"] or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome}/{global_info['ano']}",
            "profissionais": list(all_professionals.values())
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
