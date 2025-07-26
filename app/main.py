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
        

# --- INÍCIO normaliza-escala-normaliza-escala-xlsx-JSON ---

# --- CONFIGURAÇÕES E MAPEAMENTOS GLOBAIS ---
MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 'MAIO': 5,
    'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10,
    'NOVEMBRO': 11, 'DEZEMBRO': 12
}

HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE": {"inicio": "19:00", "fim": "07:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"}
}

SETORES_NOITE_COMPLETA = ["UTI", "TERAPIA INTENSIVA", "EMERGÊNCIA"]

# --- FUNÇÕES AUXILIARES ---

def extrair_metadados_do_json(data):
    """Extrai metadados (unidade, setor, mês/ano) dos dados JSON"""
    texto_completo = ""
    
    # Concatena todo o texto das rows para buscar metadados
    for item in data:
        if "row" in item and item["row"]:
            for cell in item["row"]:
                if cell and isinstance(cell, str):
                    texto_completo += cell + " "
    
    # Busca padrões
    unidade_match = re.search(r'HOSPITAL.*?(?=\n|$)', texto_completo, re.IGNORECASE)
    setor_match = re.search(r'(?:SETOR|ESPECIALIDADES?)[\s:]*([^\n]+)', texto_completo, re.IGNORECASE)
    mes_ano_match = re.search(r'MÊS:\s*([A-ZÇÃÕ]+)[\s/]*(\d{4})', texto_completo.upper())
    
    unidade = unidade_match.group(0).strip() if unidade_match else "HOSPITAL DAS CLINICAS DRº WILSON FRANCO RODRIGUES-HC"
    setor = setor_match.group(1).strip() if setor_match else "ESPECIALIDADES MÉDICAS CLÍNICAS"
    
    mes, ano = 5, 2025  # Default baseado no exemplo
    if mes_ano_match:
        mes_nome, ano_str = mes_ano_match.groups()
        mes = MONTH_MAP.get(mes_nome, 5)
        ano = int(ano_str)
    
    return {"unidade": unidade, "setor": setor, "mes": mes, "ano": ano}

def interpretar_turno(token: str, setor: str):
    """Interpreta tokens de turno (M, T, N, D, etc.) e retorna lista de turnos"""
    if not token or not isinstance(token, str):
        return []
    
    # Limpa o token
    token_clean = token.upper().replace('\n', ' ').replace('/', '').strip()
    
    # Padrões especiais
    if "N1N2" in token_clean or "N1 N2" in token_clean:
        return [{"turno": "NOITE"}]
    
    if "CH" in token_clean:
        return []  # Plantão cancelado/coberto
    
    # Extrai códigos de turno
    turnos_encontrados = []
    
    if "MTN" in token_clean:
        turnos_encontrados = ["M", "T", "N"]
    elif "DN" in token_clean or "D N" in token_clean:
        turnos_encontrados = ["D", "N"]
    else:
        # Busca individual - melhorada para capturar M, T, N mesmo com outros caracteres
        turnos_encontrados = re.findall(r'[MTNDC]', token_clean)
        # Remove duplicatas mantendo ordem
        turnos_encontrados = list(dict.fromkeys(turnos_encontrados))
    
    if not turnos_encontrados:
        return []
    
    # Converte para turnos finais
    turnos_finais = []
    for turno_code in turnos_encontrados:
        if turno_code == 'M':
            turnos_finais.append({"turno": "MANHÃ"})
        elif turno_code == 'T':
            turnos_finais.append({"turno": "TARDE"})
        elif turno_code == 'D':  # Diurno = Manhã + Tarde
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif turno_code in ['N', 'C']:  # Noturno
            if any(s in setor.upper() for s in SETORES_NOITE_COMPLETA):
                turnos_finais.append({"turno": "NOITE"})
            else:
                turnos_finais.append({"turno": "NOITE (início)"})
                turnos_finais.append({"turno": "NOITE (fim)"})
    
    return turnos_finais

def is_valid_professional_row(row, nome_idx: int = 0):
    """Verifica se a linha representa um profissional válido"""
    if not row or nome_idx >= len(row):
        return False
    
    name = row[nome_idx]
    if not name or not isinstance(name, str):
        return False
    
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "INFORMA", "CAPACITAÇÃO", "PROCESSO"]
    
    return (all(keyword not in name_upper for keyword in ignored) and 
            len(name.split()) > 1)

# --- ENDPOINT PRINCIPAL ---

@app.post("/normaliza-escala")  
async def normaliza_escala(request: Request):
    """
    Normaliza dados de escala médica vindos da API de conversão XLSX->JSON
    
    Entrada: Array de objetos com 'row' contendo dados tabulares
    Saída: Formato estruturado com profissionais e plantões organizados
    """
    try:
        body = await request.json()
        
        if not body or not isinstance(body, list):
            raise HTTPException(status_code=400, detail="Formato inválido. Esperado array de objetos.")
        
        # Extrai metadados
        metadados = extrair_metadados_do_json(body)
        
        # Processa cada linha individualmente (cada linha = um profissional)
        profissionais_data = []
        
        for item in body:
            if not isinstance(item, dict) or "row" not in item:
                continue
                
            row = item["row"]
            if not row or not isinstance(row, list) or len(row) < 7:
                continue
            
            # Verifica se é uma linha de profissional válida
            if not is_valid_professional_row(row, 0):
                continue
            
            # Extrai informações do profissional de cada linha
            nome = str(row[0]).replace('\n', ' ').strip()
            cargo = str(row[1]).strip() if len(row) > 1 and row[1] else "N/I"
            crm = str(row[2]).strip() if len(row) > 2 and row[2] else "N/I"
            vinculo = str(row[3]).strip() if len(row) > 3 and row[3] else "N/I"
            
            # Coleta plantões das posições 7+ (dias do mês)
            plantoes_brutos = defaultdict(list)
            for dia in range(1, 32):
                col_idx = dia + 6  # Offset: 0-6 são metadados, 7+ são dias
                if col_idx < len(row) and row[col_idx]:
                    token_plantao = str(row[col_idx]).strip()
                    if token_plantao and token_plantao.lower() not in ['null', 'none', '']:
                        plantoes_brutos[dia].append(token_plantao)
            
            # Adiciona à lista se tem plantões
            if plantoes_brutos:
                profissionais_data.append({
                    "info": {
                        "medico_nome": nome,
                        "cargo": cargo,
                        "crm": crm,
                        "vinculo": vinculo
                    },
                    "plantoes_brutos": plantoes_brutos
                })
        
        # Monta JSON final com lógica de turnos
        lista_profissionais_final = []
        
        for prof_data in profissionais_data:
            info = prof_data["info"]
            plantoes_brutos = prof_data["plantoes_brutos"]
            
            profissional_obj = {
                "medico_nome": info["medico_nome"].upper(),
                "medico_crm": f"CRM: {info['crm']}" if not str(info['crm']).startswith("CRM") else info["crm"],
                "medico_especialidade": info["cargo"].upper(),
                "medico_vinculo": info["vinculo"].upper(),
                "medico_setor": metadados["setor"].upper(),
                "plantoes": []
            }
            
            # Processa plantões por dia
            for dia, tokens in sorted(plantoes_brutos.items()):
                for token in set(tokens):  # Remove duplicatas
                    turnos = interpretar_turno(token, metadados["setor"])
                    
                    for turno_info in turnos:
                        try:
                            data_plantao = datetime(metadados["ano"], metadados["mes"], dia)
                            
                            # Ajuste para NOITE (fim) que termina no dia seguinte
                            if turno_info["turno"] == "NOITE (fim)":
                                data_plantao = datetime(metadados["ano"], metadados["mes"], dia + 1)
                            
                            horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                            
                            plantao = {
                                "dia": dia,
                                "data": data_plantao.strftime('%d/%m/%Y'),
                                "turno": turno_info["turno"],
                                "inicio": horarios.get("inicio"),
                                "fim": horarios.get("fim")
                            }
                            profissional_obj["plantoes"].append(plantao)
                            
                        except ValueError:
                            # Dia inválido
                            continue
            
            if profissional_obj["plantoes"]:
                # Ordena plantões
                profissional_obj["plantoes"].sort(key=lambda p: (p["dia"], p["inicio"] or ""))
                lista_profissionais_final.append(profissional_obj)
        
        # Resposta final
        mes_nome_str = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(metadados["mes"])]
        final_output = [{
            "unidade_escala": metadados["unidade"],
            "mes_ano_escala": f"{mes_nome_str}/{metadados['ano']}",
            "profissionais": lista_profissionais_final
        }]
        
        return JSONResponse(content=final_output)
        
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "trace": traceback.format_exc()}, 
            status_code=500
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "escala-normalizacao"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# --- FIM normaliza-escala-xlsx-JSON ---

# --- INÍCIO normaliza-escala-normaliza-escala-xlsx-JSON ---
# --- CONFIGURAÇÕES E MAPEAMENTOS GLOBAIS ---
MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 'MAIO': 5,
    'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10,
    'NOVEMBRO': 11, 'DEZEMBRO': 12
}
HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"}, "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE": {"inicio": "19:00", "fim": "07:00"}, "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"}
}
SETORES_NOITE_COMPLETA = ["UTI", "TERAPIA INTENSIVA"]

# --- FUNÇÕES AUXILIARES ---
def parse_mes_ano(text):
    match = re.search(r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    mes = MONTH_MAP.get(mes_nome)
    ano = int(ano_str)
    return mes, ano

def interpretar_turno(token, medico_setor):
    if not token or not isinstance(token, str): return []
    token_upper = token.upper().replace('\n', '').replace('/', '').replace(' ', '')
    
    if "N1N2" in token_upper: return [{"turno": "NOITE"}]
    
    if "MTN" in token_upper: tokens = ["M", "T", "N"]
    elif "DN" in token_upper: tokens = ["D", "N"]
    elif "M/T" in token_upper or "MT" in token_upper: tokens = ["M", "T"]
    elif "T/N" in token_upper or "TN" in token_upper: tokens = ["T", "N"]
    elif "M/N" in token_upper or "MN" in token_upper: tokens = ["M", "N"]
    else: tokens = re.findall(r'[MTNDC]', token_upper)
    
    turnos_finais = []
    for t in list(dict.fromkeys(tokens)):
        if t == 'M': turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T': turnos_finais.append({"turno": "TARDE"})
        elif t == 'D': 
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t in ['N', 'C']:
            is_noite_completa = any(s in medico_setor.upper() for s in SETORES_NOITE_COMPLETA)
            if is_noite_completa:
                turnos_finais.append({"turno": "NOITE"})
            else:
                turnos_finais.append({"turno": "NOITE (início)"})
                turnos_finais.append({"turno": "NOITE (fim)"})
    return turnos_finais

def is_valid_professional_name(name):
    if not name or not isinstance(name, str): return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", "CARGO", "MATRÍCULA"]
    if any(keyword in name_upper for keyword in ignored): return False
    return len(name.split()) >= 2

# --- ENDPOINT PRINCIPAL ---
@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        
        full_text, all_table_rows = "", []
        for page_data in body:
            b64_data = page_data.get("bae64")
            if not b64_data: continue
            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                full_text += page.get_text("text") + "\n"
                for table in page.find_tables():
                    extracted = table.extract()
                    if extracted: all_table_rows.extend(extracted)
        
        unidade_match = re.search(r'UNIDADE:\s*(.*?)\n', full_text, re.IGNORECASE)
        setor_match = re.search(r'UNIDADE SETOR:\s*(.*?)\n', full_text, re.IGNORECASE)
        mes, ano = parse_mes_ano(full_text)
        
        unidade = unidade_match.group(1).strip() if unidade_match else "NÃO INFORMADO"
        setor = setor_match.group(1).strip() if setor_match else "NÃO INFORMADO"
        
        if mes is None or ano is None:
            return JSONResponse(content={"error": "Mês/Ano não encontrados."}, status_code=400)
        
        header_row, header_index = None, -1
        for i, row in enumerate(all_table_rows):
            if row and any("NOME COMPLETO" in str(cell).upper().replace('\n', ' ') for cell in row):
                header_row = row
                header_index = i
                break
        
        if not header_row:
            return JSONResponse(content={"error": "Cabeçalho da escala não encontrado."}, status_code=400)

        col_map = {str(name).replace('\n', ' '): i for i, name in enumerate(header_row) if name}
        if "CONSELHO DE CLASSE" in col_map: col_map["CRM"] = col_map["CONSELHO DE CLASSE"]
        
        # Etapa de "Desfazer Merge" e limpeza
        cleaned_rows = []
        last_name = None
        nome_idx = col_map.get("NOME COMPLETO")

        for row in all_table_rows[header_index + 1:]:
            if not row or len(row) < nome_idx + 1: continue
            
            nome_bruto = row[nome_idx]
            if nome_bruto and is_valid_professional_name(nome_bruto):
                last_name = nome_bruto.replace('\n', ' ').strip()
            
            if last_name:
                new_row = list(row) # Cria uma cópia
                new_row[nome_idx] = last_name
                cleaned_rows.append(new_row)

        # Agrupa os dados limpos
        profissionais_data = defaultdict(lambda: {"info_rows": []})
        for row in cleaned_rows:
            nome = row[nome_idx]
            profissionais_data[nome]["info_rows"].append(row)

        # Monta a saída final
        lista_profissionais_final = []
        for nome, data in profissionais_data.items():
            info_rows = data["info_rows"]
            primeira_linha = info_rows[0]
            
            profissional_obj = {
                "medico_nome": nome,
                "medico_crm": str(primeira_linha[col_map.get("CRM")]).strip() if col_map.get("CRM") and col_map.get("CRM") < len(primeira_linha) and primeira_linha[col_map.get("CRM")] else "N/I",
                "medico_especialidade": str(primeira_linha[col_map.get("CARGO")]).strip() if col_map.get("CARGO") and col_map.get("CARGO") < len(primeira_linha) else "N/I",
                "medico_vinculo": str(primeira_linha[col_map.get("VÍNCULO")]).strip() if col_map.get("VÍNCULO") and col_map.get("VÍNCULO") < len(primeira_linha) else "N/I",
                "medico_setor": setor,
                "plantoes": []
            }
            
            plantoes_brutos = defaultdict(list)
            for row in info_rows:
                for dia_str, col_idx in col_map.items():
                    try:
                        dia = int(dia_str)
                        if col_idx < len(row) and row[col_idx]:
                            plantoes_brutos[dia].append(str(row[col_idx]))
                    except (ValueError, TypeError): continue
            
            for dia, tokens in sorted(plantoes_brutos.items()):
                for token in set(tokens):
                    turnos = interpretar_turno(token, setor)
                    for turno_info in turnos:
                        data_plantao = datetime(ano, mes, dia)
                        if turno_info["turno"] == "NOITE (fim)":
                            data_plantao += timedelta(days=1)
                        horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                        profissional_obj["plantoes"].append({
                            "dia": dia, "data": data_plantao.strftime('%d/%m/%Y'),
                            "turno": turno_info["turno"], "inicio": horarios.get("inicio"), "fim": horarios.get("fim")
                        })
            
            if profissional_obj["plantoes"]:
                profissional_obj["plantoes"].sort(key=lambda p: (p["dia"], p["inicio"] or ""))
                lista_profissionais_final.append(profissional_obj)
        
        mes_nome_str = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(mes)]
        final_output = [{
            "unidade_escala": unidade,
            "mes_ano_escala": f"{mes_nome_str}/{ano}",
            "profissionais": lista_profissionais_final
        }]
        
        return JSONResponse(content=final_output)

    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)

# --- FIM normaliza-escala-xlsx-JSON ---

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

# --- Início normaliza-escala-json ---
# --- CONFIGURAÇÕES E MAPEAMENTOS GLOBAIS ---

MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 
    'MAIO': 5, 'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 
    'SETEMBRO': 9, 'OUTUBRO': 10, 'NOVEMBRO': 11, 'DEZEMBRO': 12
}

# Definição dos horários padrão para cada turno
HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE": {"inicio": "19:00", "fim": "07:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"}
}

# Lista de setores que têm "noite completa" (19h às 07h)
SETORES_NOITE_COMPLETA = ["UTI", "TERAPIA INTENSIVA"] 

# --- FUNÇÕES AUXILIARES ---

def parse_mes_ano(text):
    match = re.search(r'([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    mes = MONTH_MAP.get(mes_nome)
    ano = int(ano_str)
    return mes, ano

def interpretar_turno(token, medico_setor):
    if not token or not isinstance(token, str):
        return []

    # Normaliza o token
    token_upper = token.upper().replace('\n', '').replace('/', '').replace(' ', '')
    
    # 10.1.1: Trata casos especiais como N1N2
    if "N1N2" in token_upper:
        return [{"turno": "NOITE"}]

    # 10.1.2: Expande siglas compostas
    tokens = []
    if "MTN" in token_upper: tokens.extend(["M", "T", "N"])
    elif "DN" in token_upper: tokens.extend(["D", "N"])
    else:
        # Pega todas as letras M, T, N, D do token
        found_tokens = re.findall(r'[MTND]', token_upper)
        tokens.extend(list(dict.fromkeys(found_tokens))) # Remove duplicados
    
    # Se não encontrou tokens, retorna vazio
    if not tokens: return []
    
    # 10.1.3: Converte os tokens
    turnos_finais = []
    for t in tokens:
        if t == 'M': turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T': turnos_finais.append({"turno": "TARDE"})
        elif t == 'D': 
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'N':
            # Verifica se o setor exige noite completa
            if any(s in medico_setor.upper() for s in SETORES_NOITE_COMPLETA):
                turnos_finais.append({"turno": "NOITE"})
            else:
                turnos_finais.append({"turno": "NOITE (início)"})
                turnos_finais.append({"turno": "NOITE (fim)"})

    return turnos_finais

# --- ENDPOINT PRINCIPAL ---

@app.post("/normaliza-escala-json")
async def normaliza_escala_json(request: Request):
    try:
        input_data = await request.json()
        
        all_scales = []
        current_scale_data = []

        # Separa o documento em blocos de escalas
        for item in input_data:
            row = item.get("row", [])
            row_text = ' '.join(str(c or '').strip() for c in row).upper()
            
            # Detecta o início de uma nova escala
            if 'UNIDADE:' in row_text or 'UNIDADE SETOR:' in row_text:
                if current_scale_data:
                    all_scales.append(current_scale_data)
                current_scale_data = []
            
            current_scale_data.append(row)
        
        if current_scale_data:
            all_scales.append(current_scale_data)
            
        # Processa cada bloco de escala
        final_output = []
        for scale_block in all_scales:
            unidade, setor, mes, ano = "NÃO INFORMADO", "NÃO INFORMADO", None, None
            header_row, header_index = None, -1

            # 1. Extrai metadados e localiza o cabeçalho
            for i, row in enumerate(scale_block):
                row_text = ' '.join(str(c or '').strip() for c in row).upper()
                if 'UNIDADE:' in row_text:
                    unidade = re.split(r'UNIDADE:', row_text, flags=re.IGNORECASE)[1].split('ESCALA DE SERVIÇO:')[0].strip()
                if 'UNIDADE SETOR:' in row_text:
                    setor = re.split(r'UNIDADE SETOR:', row_text, flags=re.IGNORECASE)[1].strip()
                if 'MÊS:' in row_text:
                    mes, ano = parse_mes_ano(row_text)
                
                if "NOME COMPLETO" in row and any(isinstance(c, int) for c in row):
                    header_row = row
                    header_index = i

            if not header_row or mes is None:
                continue # Pula blocos que não são escalas válidas

            # Mapeia a posição do dia para seu índice na linha
            day_to_col_index = {day: i for i, day in enumerate(header_row) if isinstance(day, int)}

            # 2. Processa as linhas de dados (após o cabeçalho)
            profissionais_data = defaultdict(lambda: {
                "info": {}, "plantoes_brutos": defaultdict(list)
            })
            
            last_professional_name = None
            
            for row in scale_block[header_index + 1:]:
                # Ignora linhas de dias da semana, vazias ou de notas
                if not row or len(row) < 3 or all(v is None for v in row) or "informamos que" in str(row[0]).lower():
                    continue

                # Flexibiliza a posição do nome
                nome = None
                if isinstance(row[0], str) and len(row[0].split()) > 1:
                    nome = row[0].replace('\n', ' ').strip()
                elif len(row) > 1 and isinstance(row[1], str) and len(row[1].split()) > 1:
                    nome = row[1].replace('\n', ' ').strip()

                if nome:
                    last_professional_name = nome

                if not last_professional_name: continue
                
                # Coleta informações se ainda não tiver
                if not profissionais_data[last_professional_name]["info"]:
                    profissionais_data[last_professional_name]["info"] = {
                        "medico_nome": last_professional_name,
                        "cargo": str(row[1] or '').strip() if len(row) > 1 else '',
                        "matricula": str(row[2] or '').strip() if len(row) > 2 else '',
                        "vinculo": str(row[3] or '').strip() if len(row) > 3 else '',
                        "crm": str(row[6] or '').strip() if len(row) > 6 else ''
                    }

                # Coleta os plantões brutos
                for day, col_index in day_to_col_index.items():
                    if col_index < len(row) and row[col_index]:
                        profissionais_data[last_professional_name]["plantoes_brutos"][day].append(str(row[col_index]))

            # 3. Monta a saída final para a escala
            lista_profissionais_final = []
            for nome, data in profissionais_data.items():
                if not data["plantoes_brutos"]: continue

                profissional_obj = {
                    "medico_nome": nome,
                    "medico_crm": data["info"]["crm"],
                    "medico_especialidade": data["info"]["cargo"],
                    "medico_vinculo": data["info"]["vinculo"],
                    "medico_setor": setor,
                    "plantoes": []
                }
                
                for dia, tokens in sorted(data["plantoes_brutos"].items()):
                    for token in set(tokens): # Usa set para evitar duplicados
                        turnos_interpretados = interpretar_turno(token, setor)
                        for turno_info in turnos_interpretados:
                            data_plantao = datetime(ano, mes, dia)
                            # 10.2: Incrementa a data para NOITE (fim)
                            if turno_info["turno"] == "NOITE (fim)":
                                data_plantao += timedelta(days=1)
                            
                            horarios = HORARIOS_TURNO[turno_info["turno"]]

                            profissional_obj["plantoes"].append({
                                "dia": dia,
                                "data": data_plantao.strftime('%d/%m/%Y'),
                                "turno": turno_info["turno"],
                                "inicio": horarios["inicio"],
                                "fim": horarios["fim"]
                            })
                
                # Ordena os plantões
                profissional_obj["plantoes"].sort(key=lambda p: (p["dia"], p["inicio"]))
                lista_profissionais_final.append(profissional_obj)

            if lista_profissionais_final:
                final_output.append({
                    "unidade_escala": unidade,
                    "mes_ano_escala": f"{list(MONTH_MAP.keys())[mes-1]}/{ano}",
                    "profissionais": lista_profissionais_final
                })

        return JSONResponse(content=final_output)

    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
