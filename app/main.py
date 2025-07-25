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
        

# --- INÍCIO normaliza-escala-PDF ---

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
    match = re.search(r'MÊS:\s*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    mes = MONTH_MAP.get(mes_nome)
    ano = int(ano_str)
    return mes, ano

def interpretar_turno(token, medico_setor):
    if not token or not isinstance(token, str): return []
    token_upper = token.upper().replace('\n', '').replace('/', '').replace(' ', '')
    
    if "N1N2" in token_upper: return [{"turno": "NOITE"}]
    
    tokens = []
    if "MTN" in token_upper: tokens.extend(["M", "T", "N"])
    elif "DN" in token_upper: tokens.extend(["D", "N"])
    else:
        found_tokens = re.findall(r'[MTNDC]', token_upper)
        tokens.extend(list(dict.fromkeys(found_tokens)))
    
    if not tokens: return []
    
    turnos_finais = []
    for t in tokens:
        if t == 'M': turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T': turnos_finais.append({"turno": "TARDE"})
        elif t == 'D': 
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t in ['N', 'C']:
            if any(s in medico_setor.upper() for s in SETORES_NOITE_COMPLETA):
                turnos_finais.append({"turno": "NOITE"})
            else:
                turnos_finais.append({"turno": "NOITE (início)"})
                turnos_finais.append({"turno": "NOITE (fim)"})
    return turnos_finais

def is_valid_professional_row(row, nome_idx):
    if not row or nome_idx >= len(row): return False
    name = row[nome_idx]
    if not name or not isinstance(name, str): return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "* INFORMA", "CAPACITAÇÃO", "PROCESSO"]
    return all(keyword not in name_upper for keyword in ignored) and len(name.split()) > 1

# --- ENDPOINT PRINCIPAL (PDF DIRETO) ---

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        
        # 1. Extrai tabelas e texto de todas as páginas PDF
        all_rows = []
        full_text = ""
        for page_data in body:
            b64_data = page_data.get("bae64") # Corrigido para "bae64" como no seu input
            if not b64_data: continue
            
            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                full_text += page.get_text() + "\n"
                tables = page.find_tables()
                for table in tables:
                    all_rows.extend(table.extract())

        # 2. Extrai metadados do texto completo
        unidade_match = re.search(r'UNIDADE:\s*(.*)', full_text, re.IGNORECASE)
        setor_match = re.search(r'UNIDADE SETOR:\s*(.*)', full_text, re.IGNORECASE)
        mes, ano = parse_mes_ano(full_text)
        
        unidade = unidade_match.group(1).strip() if unidade_match else "NÃO INFORMADO"
        setor = setor_match.group(1).strip() if setor_match else "NÃO INFORMADO"
        
        if not all_rows or not mes:
             return JSONResponse(content={"error": "Nenhuma tabela ou data válida encontrada."}, status_code=400)

        # 3. Localiza o cabeçalho e cria o mapa de colunas
        header_row, col_map = None, {}
        for row in all_rows:
            if row and "NOME COMPLETO" in str(row[0]):
                header_row = row
                for idx, col_name in enumerate(row):
                    if col_name: col_map[col_name.replace('\n', ' ')] = idx
                if "CONSELHO DE CLASSE" in col_map: col_map["CRM"] = col_map["CONSELHO DE CLASSE"]
                break
        
        if not header_row:
            return JSONResponse(content={"error": "Cabeçalho da escala não encontrado nas tabelas."}, status_code=400)
        
        # 4. Processa as linhas e agrupa por profissional
        profissionais_data = defaultdict(lambda: {"info": {}, "plantoes_brutos": defaultdict(list)})
        last_professional_name = None
        nome_idx = col_map.get("NOME COMPLETO", 0)

        for row in all_rows:
            if is_valid_professional_row(row, nome_idx):
                last_professional_name = row[nome_idx].replace('\n', ' ').strip()
            
            if not last_professional_name: continue

            # Coleta as informações do profissional (sem propagar CRM indevidamente)
            info = profissionais_data[last_professional_name]["info"]
            if not info: # Pega as informações da primeira vez que o nome aparece
                info["medico_nome"] = last_professional_name
                info["cargo"] = row[col_map.get("CARGO")] if col_map.get("CARGO") and col_map.get("CARGO") < len(row) else "N/I"
                info["vinculo"] = row[col_map.get("VÍNCULO")] if col_map.get("VÍNCULO") and col_map.get("VÍNCULO") < len(row) else "N/I"
                crm_val = row[col_map.get("CRM")] if col_map.get("CRM") and col_map.get("CRM") < len(row) else None
                info["crm"] = str(crm_val).strip() if crm_val else "N/I"

            # Coleta os plantões da linha atual
            for dia_str, col_idx in col_map.items():
                try:
                    dia = int(dia_str)
                    if col_idx < len(row) and row[col_idx]:
                        profissionais_data[last_professional_name]["plantoes_brutos"][dia].append(str(row[col_idx]))
                except (ValueError, TypeError):
                    continue # Ignora cabeçalhos que não são números de dia

        # 5. Monta o JSON de saída final com a lógica de turnos
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

# --- FIM normaliza-escala-PDF ---


from fastapi import Request
from fastapi.responses import JSONResponse
from fpdf import FPDF
import base64
import os
import textwrap

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
