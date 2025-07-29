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
import traceback
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import fitz

app = FastAPI()

MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 'MAIO': 5,
    'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10,
    'NOVEMBRO': 11, 'DEZEMBRO': 12
}

# Horários fixos do modelo convencional
TURNOS = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

def parse_mes_ano(text):
    match = re.search(r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    mes = MONTH_MAP.get(mes_nome)
    ano = int(ano_str)
    return mes, ano

def interpretar_turno(token):
    """
    Interpreta uma string de turnos e retorna todos os turnos daquele dia conforme regras:
    M = Manhã (07:00-13:00)
    T = Tarde (13:00-19:00)
    N = Noite (19:00-01:00) + (01:00-07:00) (dois lançamentos)
    D = Manhã e Tarde (07:00-13:00 e 13:00-19:00)
    Combinações: Ex. 'MTN' -> todos
    """
    token_clean = token.replace('\n', '').replace('/', '').replace(' ', '').upper()
    turnos = []
    for c in token_clean:
        if c == "M":
            turnos.append({"turno": "MANHÃ", **TURNOS["MANHÃ"]})
        elif c == "T":
            turnos.append({"turno": "TARDE", **TURNOS["TARDE"]})
        elif c == "N":
            turnos.append({"turno": "NOITE (início)", **TURNOS["NOITE (início)"]})
            turnos.append({"turno": "NOITE (fim)", **TURNOS["NOITE (fim)"]})
        elif c == "D":
            turnos.append({"turno": "MANHÃ", **TURNOS["MANHÃ"]})
            turnos.append({"turno": "TARDE", **TURNOS["TARDE"]})
    return turnos

def is_valid_professional_name(name):
    if not name or not isinstance(name, str): return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", "CARGO", "MATRÍCULA"]
    if any(keyword in name_upper for keyword in ignored): return False
    return len(name.split()) >= 2 or name.isupper()

def dedup_plantao(lista):
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
        if not (isinstance(body, dict) and "pages" in body and isinstance(body["pages"], list) and body["pages"]):
            return JSONResponse(content={
                "error": "Formato de entrada inválido: esperado { 'pages': [ { 'file_base64': ... }, ... ] }"
            }, status_code=400)

        all_table_rows = []
        last_header_row = None
        last_header_map = None
        last_setor = None
        last_unidade = None
        last_mes, last_ano = None, None

        for page_idx, page_data in enumerate(body["pages"]):
            b64_data = page_data.get("file_base64")
            if not b64_data:
                return JSONResponse(content={
                    "error": f"Página {page_idx+1} sem 'file_base64' no input."
                }, status_code=400)
            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")

                # Extração robusta do SETOR (pega tudo após "UNIDADE SETOR:" até fim da linha)
                setor_match = re.search(r'UNIDADE\s*SETOR[:\s\-]*([^\n\r]+)', page_text, re.IGNORECASE)
                setor = setor_match.group(1).strip() if setor_match else last_setor

                # UNIDADE
                unidade_match = re.search(r'UNIDADE[:\s\-]*([^\n\r]+)', page_text, re.IGNORECASE)
                unidade = unidade_match.group(1).strip() if unidade_match else last_unidade

                # MÊS/ANO
                mes, ano = parse_mes_ano(page_text)
                if mes is None: mes = last_mes
                if ano is None: ano = last_ano
                if unidade: last_unidade = unidade
                if setor: last_setor = setor
                if mes: last_mes = mes
                if ano: last_ano = ano

                tabelas = []
                for table in page.find_tables():
                    extracted = table.extract()
                    if extracted: tabelas.append(extracted)
                tabela = tabelas[0] if tabelas else []

                # Procura linha de cabeçalho
                header_row = None
                for row in tabela:
                    if row and any("NOME" in str(cell).upper() and "COMPLETO" in str(cell).upper() for cell in row):
                        # Corrige deslocamento por índice se presente
                        if row[0] is None or (isinstance(row[0], str) and row[0].strip().isdigit()):
                            header_row = row[1:]
                        else:
                            header_row = row
                        header_row = [str(cell).replace('\n', ' ').strip().upper() if cell else "" for cell in header_row]
                        break

                if header_row:
                    last_header_row = header_row
                    header_map = {}
                    for i, col_name in enumerate(header_row):
                        if "NOME COMPLETO" in col_name: header_map["NOME COMPLETO"] = i
                        elif "CARGO" in col_name: header_map["CARGO"] = i
                        elif "VÍNCULO" in col_name or "VINCULO" in col_name: header_map["VÍNCULO"] = i
                        elif "CONSELHO" in col_name or "CRM" in col_name: header_map["CRM"] = i
                        elif col_name.isdigit(): header_map[int(col_name)] = i
                    last_header_map = header_map
                    all_table_rows.append(header_row)
                    start_data = tabela.index(row) + 1
                    for data_row in tabela[start_data:]:
                        all_table_rows.append(data_row[1:] if (data_row and (data_row[0] is None or (isinstance(data_row[0], str) and data_row[0].strip().isdigit()))) else data_row)
                else:
                    if tabela and last_header_row:
                        all_table_rows.append(last_header_row)
                        for data_row in tabela:
                            all_table_rows.append(data_row[1:] if (data_row and (data_row[0] is None or (isinstance(data_row[0], str) and data_row[0].strip().isdigit()))) else data_row)

        if last_mes is None or last_ano is None:
            return JSONResponse(content={"error": "Mês/Ano não encontrados."}, status_code=400)

        profissionais_data = defaultdict(lambda: {"info_rows": []})
        header_map = None
        nome_idx = None
        idx_linha = 0
        last_name = None

        while idx_linha < len(all_table_rows):
            row = all_table_rows[idx_linha]
            if row and any("NOME" in str(cell).upper() and "COMPLETO" in str(cell).upper() for cell in row):
                header_row = [str(cell).replace('\n', ' ').strip().upper() if cell else "" for cell in row]
                header_map = {}
                for i, col_name in enumerate(header_row):
                    if "NOME COMPLETO" in col_name: header_map["NOME COMPLETO"] = i
                    elif "CARGO" in col_name: header_map["CARGO"] = i
                    elif "VÍNCULO" in col_name or "VINCULO" in col_name: header_map["VÍNCULO"] = i
                    elif "CONSELHO" in col_name or "CRM" in col_name: header_map["CRM"] = i
                    elif col_name.isdigit(): header_map[int(col_name)] = i
                nome_idx = header_map.get("NOME COMPLETO")
                last_name = None
                idx_linha += 1
                continue

            if not header_map or nome_idx is None:
                idx_linha += 1
                continue

            if row and (row[0] is None or (isinstance(row[0], str) and row[0].strip().isdigit())):
                row = row[1:]
            if not row or len(row) <= nome_idx:
                idx_linha += 1
                continue

            nome_bruto = row[nome_idx]
            if nome_bruto and is_valid_professional_name(nome_bruto):
                last_name = nome_bruto.replace('\n', ' ').strip()
            elif nome_bruto and last_name is not None and len(nome_bruto.strip().split()) == 1:
                last_name = f"{last_name} {nome_bruto.strip()}"
            if last_name is not None:
                new_row = list(row)
                new_row[nome_idx] = last_name
                profissionais_data[last_name]["info_rows"].append(new_row)
            idx_linha += 1

        lista_profissionais_final = []
        for nome, data in profissionais_data.items():
            info_rows = data["info_rows"]
            primeira_linha = info_rows[0]
            profissional_obj = {
                "medico_nome": nome,
                "medico_crm": str(primeira_linha[header_map.get("CRM")]).strip() if header_map.get("CRM") and header_map.get("CRM") < len(primeira_linha) and primeira_linha[header_map.get("CRM")] else "N/I",
                "medico_especialidade": str(primeira_linha[header_map.get("CARGO")]).strip() if header_map.get("CARGO") and header_map.get("CARGO") < len(primeira_linha) else "N/I",
                "medico_vinculo": str(primeira_linha[header_map.get("VÍNCULO")]).strip() if header_map.get("VÍNCULO") and header_map.get("VÍNCULO") < len(primeira_linha) else "N/I",
                "medico_setor": last_setor or "NÃO INFORMADO",
                "plantoes": []
            }
            plantoes_brutos = defaultdict(list)
            for row in info_rows:
                for dia, col_idx in header_map.items():
                    if isinstance(dia, int):
                        if col_idx < len(row) and row[col_idx] and str(row[col_idx]).strip():
                            plantoes_brutos[dia].append(str(row[col_idx]).strip())

            for dia, tokens in sorted(plantoes_brutos.items()):
                for token in tokens:
                    turnos = interpretar_turno(token)
                    data_plantao = datetime(last_ano, last_mes, dia)
                    for turno_info in turnos:
                        # Se for "NOITE (fim)", joga para o dia seguinte
                        if turno_info["turno"] == "NOITE (fim)":
                            data_fim = data_plantao + timedelta(days=1)
                            profissional_obj["plantoes"].append({
                                "dia": data_fim.day,
                                "data": data_fim.strftime('%d/%m/%Y'),
                                "turno": turno_info["turno"],
                                "inicio": turno_info["inicio"],
                                "fim": turno_info["fim"]
                            })
                        else:
                            profissional_obj["plantoes"].append({
                                "dia": data_plantao.day,
                                "data": data_plantao.strftime('%d/%m/%Y'),
                                "turno": turno_info["turno"],
                                "inicio": turno_info["inicio"],
                                "fim": turno_info["fim"]
                            })
            profissional_obj["plantoes"] = dedup_plantao(profissional_obj["plantoes"])
            # Ordena os plantões pelo horário de início
            profissional_obj["plantoes"].sort(key=lambda p: (p["dia"], p["inicio"] or ""))
            if profissional_obj["plantoes"]:
                lista_profissionais_final.append(profissional_obj)

        mes_nome_str = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(last_mes)]
        final_output = [{
            "unidade_escala": last_unidade or "NÃO INFORMADO",
            "setor_escala": last_setor or "NÃO INFORMADO",
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
