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
from PyPDF2 import PdfReader
from io import BytesIO

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

TURNOS = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE": {"inicio": "19:00", "fim": "07:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

def extrair_texto_pdf(pdf_file):
    texto_paginas = []
    with fitz.open(stream=pdf_file, filetype="pdf") as doc:
        for pagina in doc:
            texto_paginas.append(pagina.get_text())
    return texto_paginas

def identificar_unidade_e_setor(texto):
    unidade_escala = ""
    medico_setor = ""
    padrao_unidade = re.search(r"UNIDADE:\s*(.+)", texto)
    padrao_setor = re.search(r"UNIDADE SETOR:\s*(.+)", texto)

    if padrao_unidade:
        unidade_escala = padrao_unidade.group(1).strip()

    if padrao_setor:
        medico_setor = padrao_setor.group(1).strip()

    return unidade_escala, medico_setor

def obter_dias_mes(texto):
    linhas = texto.split("\n")
    for linha in linhas:
        dias = re.findall(r"\b\d{1,2}\b", linha)
        if len(dias) >= 28:
            return [int(d) for d in dias]
    return []

def normalizar_plantao(dia, entrada, saida, mes_ano, turno_label=None):
    data_inicio = datetime.strptime(f"{dia}/{mes_ano}", "%d/%m/%Y")
    if turno_label == "NOITE (início)":
        return {
            "dia": dia,
            "data": data_inicio.strftime("%d/%m/%Y"),
            "turno": "NOITE (início)",
            "inicio": TURNOS["NOITE (início")["inicio"],
            "fim": TURNOS["NOITE (início)"]["fim"],
        }
    elif turno_label == "NOITE (fim)":
        data_inicio += timedelta(days=1)
        return {
            "dia": data_inicio.day,
            "data": data_inicio.strftime("%d/%m/%Y"),
            "turno": "NOITE (fim)",
            "inicio": TURNOS["NOITE (fim)"]["inicio"],
            "fim": TURNOS["NOITE (fim)"]["fim"],
        }
    else:
        return {
            "dia": dia,
            "data": data_inicio.strftime("%d/%m/%Y"),
            "turno": turno_label,
            "inicio": entrada,
            "fim": saida,
        }

def extrair_plantao_por_linha(linha, dias, mes_ano):
    plantoes = []
    for i, entrada in enumerate(linha):
        dia = dias[i] if i < len(dias) else None
        entrada = entrada.strip().upper()

        if not entrada or entrada in {"FÉRIAS", "ATESTADO"}:
            continue

        if "N" in entrada:
            if entrada == "N":
                plantoes.append(normalizar_plantao(dia, None, None, mes_ano, "NOITE (início)"))
                plantoes.append(normalizar_plantao(dia, None, None, mes_ano, "NOITE (fim)"))
            elif entrada == "n":
                plantoes.append(normalizar_plantao(dia, TURNOS["NOITE (início)"]["inicio"], TURNOS["NOITE (início)"]["fim"], mes_ano, "NOITE"))
        elif entrada == "M":
            plantoes.append(normalizar_plantao(dia, TURNOS["MANHÃ"]["inicio"], TURNOS["MANHÃ"]["fim"], mes_ano, "MANHÃ"))
        elif entrada == "T":
            plantoes.append(normalizar_plantao(dia, TURNOS["TARDE"]["inicio"], TURNOS["TARDE"]["fim"], mes_ano, "TARDE"))
        elif entrada == "D":
            plantoes.append(normalizar_plantao(dia, TURNOS["MANHÃ"]["inicio"], TURNOS["NOITE"]["fim"], mes_ano, "DIA TODO"))
        elif entrada == "PJ":
            plantoes.append(normalizar_plantao(dia, "07:00", "19:00", mes_ano, "PJ"))
        elif entrada == "PJ N":
            plantoes.append(normalizar_plantao(dia, TURNOS["NOITE (início)"]["inicio"], TURNOS["NOITE (início)"]["fim"], mes_ano, "PJ NOITE (início)"))
            plantoes.append(normalizar_plantao(dia, TURNOS["NOITE (fim)"]["inicio"], TURNOS["NOITE (fim)"]["fim"], mes_ano, "PJ NOITE (fim)"))

    return plantoes

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(file: UploadFile = File(...)):
    conteudo = await file.read()
    paginas = extrair_texto_pdf(conteudo)

    resultados = []
    for pagina_texto in paginas:
        unidade_escala, medico_setor = identificar_unidade_e_setor(pagina_texto)
        dias = obter_dias_mes(pagina_texto)

        # Extrair mês/ano
        match_mes = re.search(r"MÊS:\s*(\w+/\d{4})", pagina_texto)
        mes_ano = match_mes.group(1) if match_mes else "01/1900"

        linhas = pagina_texto.split("\n")
        for i, linha in enumerate(linhas):
            if re.match(r"^\d+\s+[A-Z ]{3,}", linha):
                colunas = linha.split()
                nome = " ".join(colunas[1:-5])
                crm = colunas[-1]
                cargo = colunas[-6]
                vinculo = colunas[-3]

                linha_plantao = linhas[i + 1].split() if i + 1 < len(linhas) else []
                plantoes = extrair_plantao_por_linha(linha_plantao, dias, mes_ano)

                resultados.append({
                    "medico_nome": nome,
                    "medico_crm": crm,
                    "medico_especialidade": cargo,
                    "medico_vinculo": vinculo,
                    "medico_setor": medico_setor,
                    "unidade_escala": unidade_escala,
                    "plantoes": plantoes
                })

    return resultados

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
