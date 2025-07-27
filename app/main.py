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

# --- CONFIGURAÇÕES E MAPEAMENTOS GLOBAIS ---
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
SETORES_NOITE_COMPLETA = ["UTI", "TERAPIA INTENSIVA"]  # Agora não interfere, só referência

def parse_mes_ano(text):
    match = re.search(r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    mes = MONTH_MAP.get(mes_nome)
    ano = int(ano_str)
    return mes, ano

def interpretar_turno(token, medico_setor):
    if not token or not isinstance(token, str): return []
    token_clean = token.replace('\n', '').replace('/', '').replace(' ', '')
    tokens = []
    # Se contém ambas N e n, trata cada caso (mas na prática normalmente é só um ou outro por célula)
    tokens.extend(list(token_clean))
    turnos_finais = []
    for t in tokens:
        if t == 'M': turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T': turnos_finais.append({"turno": "TARDE"})
        elif t == 'D':
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'N':  # Maiúsculo
            turnos_finais.append({"turno": "NOITE (início)"})
            turnos_finais.append({"turno": "NOITE (fim)"})
        elif t == 'n':  # Minúsculo
            turnos_finais.append({"turno": "NOITE (início)"})
        # Se usar outros tokens, tratar aqui
    return turnos_finais

def is_valid_professional_name(name):
    if not name or not isinstance(name, str): return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", "CARGO", "MATRÍCULA"]
    if any(keyword in name_upper for keyword in ignored): return False
    return len(name.split()) >= 2 or name.isupper()

def dedup_plantao(lista):
    # Remove plantões duplicados por (dia, turno, inicio, fim)
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
        full_text, all_table_rows = "", []
        last_header_row = None
        last_col_map = None
        last_setor = None
        last_unidade = None
        last_mes, last_ano = None, None

        # --- EXTRAÇÃO DE DADOS DE TODAS AS PÁGINAS ---
        for page_idx, page_data in enumerate(body):
            b64_data = page_data.get("bae64")
            if not b64_data: continue
            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                full_text += page_text + "\n"
                # Extrai tabelas desta página
                for table in page.find_tables():
                    extracted = table.extract()
                    if extracted: all_table_rows.extend(extracted)

            # Atualiza cabeçalho apenas se encontrado nesta página
            unidade_match = re.search(r'UNIDADE:\s*(.*?)\n', page_text, re.IGNORECASE)
            setor_match = re.search(r'UNIDADE[\s/_\-]*SETOR:\s*(.*?)\n', page_text, re.IGNORECASE)
            mes, ano = parse_mes_ano(page_text)

            unidade = unidade_match.group(1).strip() if unidade_match else last_unidade
            setor = setor_match.group(1).strip() if setor_match else last_setor
            if mes is None: mes = last_mes
            if ano is None: ano = last_ano

            if unidade: last_unidade = unidade
            if setor: last_setor = setor
            if mes: last_mes = mes
            if ano: last_ano = ano

        # Validação do mês/ano final
        if last_mes is None or last_ano is None:
            return JSONResponse(content={"error": "Mês/Ano não encontrados."}, status_code=400)

        # --- BUSCA DO CABEÇALHO PRINCIPAL (LINHA COM NOME COMPLETO ETC) ---
        header_row, header_index = None, -1
        for i, row in enumerate(all_table_rows):
            if row and any("NOME" in str(cell).upper() and "COMPLETO" in str(cell).upper() for cell in row):
                header_row = row
                header_index = i
                break

        if not header_row:
            # Não encontrou cabeçalho, mas é possível que seja continuação: usa o anterior
            if last_header_row is not None:
                header_row = last_header_row
                header_index = -1
            else:
                return JSONResponse(content={"error": "Cabeçalho da escala não encontrado."}, status_code=400)
        else:
            last_header_row = header_row

        # ---- MAPEAMENTO DE COLUNAS ROBUSTO ----
        col_map = {}
        for i, col_name in enumerate(header_row):
            if not col_name: continue
            clean_name = str(col_name).replace('\n', ' ').strip().upper()
            if "NOME COMPLETO" in clean_name: col_map["NOME COMPLETO"] = i
            elif "CARGO" in clean_name: col_map["CARGO"] = i
            elif "VÍNCULO" in clean_name or "VINCULO" in clean_name: col_map["VÍNCULO"] = i
            elif "CONSELHO" in clean_name or "CRM" in clean_name: col_map["CRM"] = i
            elif isinstance(col_name, (int, float)) or (col_name.isdigit() if isinstance(col_name, str) else False):
                 col_map[int(col_name)] = i

        nome_idx = col_map.get("NOME COMPLETO")
        if nome_idx is None:
            return JSONResponse(content={"error": "Coluna 'NOME COMPLETO' não pode ser mapeada no cabeçalho."}, status_code=400)

        # --- DESFAZENDO MERGE E CONCATENANDO NOMES QUEBRADOS ---
        cleaned_rows = []
        last_name = None
        for row in all_table_rows[header_index + 1:] if header_index != -1 else all_table_rows:
            if not row or len(row) <= nome_idx: continue
            nome_bruto = row[nome_idx]
            # Buffer inteligente para nomes quebrados
            if nome_bruto and is_valid_professional_name(nome_bruto):
                if last_name and len(nome_bruto.strip().split()) == 1:
                    last_name = f"{last_name} {nome_bruto.strip()}"
                else:
                    last_name = nome_bruto.replace('\n', ' ').strip()
            elif nome_bruto and last_name and len(nome_bruto.strip().split()) == 1:
                last_name = f"{last_name} {nome_bruto.strip()}"
            if last_name:
                new_row = list(row)
                new_row[nome_idx] = last_name
                cleaned_rows.append(new_row)

        profissionais_data = defaultdict(lambda: {"info_rows": []})
        for row in cleaned_rows:
            nome = row[nome_idx]
            profissionais_data[nome]["info_rows"].append(row)

        # --- MONTAGEM DA SAÍDA FINAL ---
        lista_profissionais_final = []
        for nome, data in profissionais_data.items():
            info_rows = data["info_rows"]
            primeira_linha = info_rows[0]

            profissional_obj = {
                "medico_nome": nome,
                "medico_crm": str(primeira_linha[col_map.get("CRM")]).strip() if col_map.get("CRM") and col_map.get("CRM") < len(primeira_linha) and primeira_linha[col_map.get("CRM")] else "N/I",
                "medico_especialidade": str(primeira_linha[col_map.get("CARGO")]).strip() if col_map.get("CARGO") and col_map.get("CARGO") < len(primeira_linha) else "N/I",
                "medico_vinculo": str(primeira_linha[col_map.get("VÍNCULO")]).strip() if col_map.get("VÍNCULO") and col_map.get("VÍNCULO") < len(primeira_linha) else "N/I",
                "medico_setor": last_setor or "NÃO INFORMADO",
                "plantoes": []
            }

            plantoes_brutos = defaultdict(list)
            for row in info_rows:
                for dia, col_idx in col_map.items():
                    if isinstance(dia, int):
                        if col_idx < len(row) and row[col_idx] and str(row[col_idx]).strip():
                            plantoes_brutos[dia].append(str(row[col_idx]).strip())
                        # Não adiciona nada se a célula está vazia!

            for dia, tokens in sorted(plantoes_brutos.items()):
                # Para cada token (pode ter mais de um por dia, linha), processa
                for token in tokens:
                    turnos = interpretar_turno(token, last_setor or "")
                    for turno_info in turnos:
                        data_plantao = datetime(last_ano, last_mes, dia)
                        if turno_info["turno"] == "NOITE (fim)":
                            data_plantao += timedelta(days=1)
                        horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                        profissional_obj["plantoes"].append({
                            "dia": data_plantao.day,
                            "data": data_plantao.strftime('%d/%m/%Y'),
                            "turno": turno_info["turno"],
                            "inicio": horarios.get("inicio"),
                            "fim": horarios.get("fim")
                        })

            profissional_obj["plantoes"] = dedup_plantao(profissional_obj["plantoes"])
            if profissional_obj["plantoes"]:
                profissional_obj["plantoes"].sort(key=lambda p: (p["dia"], p["inicio"] or ""))
                lista_profissionais_final.append(profissional_obj)

        mes_nome_str = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(last_mes)]
        final_output = [{
            "unidade_escala": last_unidade or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome_str}/{last_ano}",
            "profissionais": lista_profissionais_final
        }]

        return JSONResponse(content=final_output)

    except Exception as e:
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
