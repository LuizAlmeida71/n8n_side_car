from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from openpyxl import Workbook, load_workbook
import tempfile
import fitz  # PyMuPDF
import base64
import os
import io
from fpdf import FPDF
import traceback
from collections import defaultdict
import re
from datetime import datetime, timedelta
import pdfplumber
from typing import List
import uvicorn # Adicionado para o bloco __main__

app = FastAPI()

# --- DEFINIÇÕES GLOBAIS E FUNÇÕES AUXILIARES (CONSOLIDADAS) ---

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

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

PROFISSIONAIS_ANCHOR_MATRICIAL = [
    {"medico_nome": "MARCO ANTÔNIO LEAL SANTOS", "medico_setor": "CAMED/BLOCOS/ISOLAMENTO/UTIN/UCINco/UCINca", "medico_unidade": "HMINSN"},
    {"medico_nome": "MOACIR BARBOSA NETO", "medico_setor": "CAMED/BLOCOS/ISOLAMENTO/UTIN/UCINco/UCINca", "medico_unidade": "HMINSN"},
    {"medico_nome": "ROBERTO ANDRADE LIMA", "medico_setor": "CAMED/BLOCOS/ISOLAMENTO/UTIN/UCINco/UCINca", "medico_unidade": "HMINSN"},
    {"medico_nome": "MARYCASSIELY RODRIGUES TIZOLIM", "medico_setor": "NIR/ISOLAMENTO/BLOCOS/UTIN/UTIM", "medico_unidade": "HMINSN"},
    {"medico_nome": "CIBELE LOUSANE PINHO MOTA", "medico_setor": "NIR/ISOLAMENTO/BLOCOS/UTIN/UTIM", "medico_unidade": "HMINSN"},
    {"medico_nome": "MANOEL MESSIAS DOS SANTOS NETO", "medico_setor": "NIR/ISOLAMENTO/BLOCOS/UTIN/UTIM", "medico_unidade": "HMINSN"}
]

def parse_mes_ano_geral(text):
    month_regex = '|'.join(MONTH_MAP.keys())
    match = re.search(r'(?:MÊS[^A-Z\d]*)?(' + month_regex + r')[^A-Z\d]*(\d{4})', text.upper().replace('Ç', 'C'))
    if not match:
        return None, None
    mes_nome, ano_str = match.groups()
    return MONTH_MAP.get(mes_nome.upper()), int(ano_str)

def interpretar_turno_pdf(token):
    token = token.replace('\n', '').replace('/', '').replace(' ', '').upper()
    turnos_finais = []
    for t in token:
        if t == 'M': turnos_finais.append("MANHÃ")
        elif t == 'T': turnos_finais.append("TARDE")
        elif t == 'D': turnos_finais.extend(["MANHÃ", "TARDE"])
        elif t == 'N': turnos_finais.extend(["NOITE (início)", "NOITE (fim)"])
        elif t == 'n': turnos_finais.append("NOITE (início)")
    return turnos_finais

def interpretar_turno_pacs(token: str):
    if not token or not isinstance(token, str):
        return []
    token_clean = token.replace('\n', ' ').replace('/', ' ').replace(' ', '')
    tokens = list(token_clean)
    turnos_finais = []
    if "TOTAL" in token.upper() or "PL" in token.upper():
        return []
    for t in tokens:
        if t == 'M': turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T': turnos_finais.append({"turno": "TARDE"})
        elif t == 'D':
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'N':
            turnos_finais.append({"turno": "NOITE (início)"})
            turnos_finais.append({"turno": "NOITE (fim)"})
        elif t == 'n': turnos_finais.append({"turno": "NOITE (início)"})
    return turnos_finais

def interpretar_turno_matricial(token):
    if not token or not isinstance(token, str):
        return []
    token_clean = token.replace('\n', '').replace(' ', '').replace('/', '')
    if "TOTAL" in token.upper() or "PL" in token.upper():
        return []
    if len(token_clean) >= 2 and token_clean[-1].upper() in ['M', 'T', 'D', 'N']:
        tokens = [token_clean[-1].upper()]
    else:
        tokens = list(token_clean.upper())
    turnos = []
    for t in tokens:
        if t == 'M': turnos.append({"turno": "MANHÃ"})
        elif t == 'T': turnos.append({"turno": "TARDE"})
        elif t == 'D': turnos.append({"turno": "MANHÃ"}); turnos.append({"turno": "TARDE"})
        elif t == 'N': turnos.append({"turno": "NOITE (início)"}); turnos.append({"turno": "NOITE (fim)"})
    return turnos

def is_valid_professional_name(name):
    if not name or not isinstance(name, str): return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", "CARGO", "MATRÍCULA", "UNIDADE", "SETOR", "MÊS", "ESCALA", "ÚLTIMA", "SERVIDOR QUE ESTA FORA DA ESCALA"]
    if any(keyword in name_upper for keyword in ignored): return False
    return len(name.strip().split()) >= 2

def extrair_metadados_pagina(page_text):
    unidade = re.search(r'UNIDADE[:\s-]*(.+?)(UNIDADE|SETOR|MÊS|ESCALA|$)', page_text.replace('\n', ' '), re.I)
    setor = re.search(r'UNIDADE[/\s-]*SETOR[:\s-]*(.+?)(MÊS|ESCALA|$)', page_text.replace('\n', ' '), re.I)
    return unidade.group(1).strip() if unidade else None, setor.group(1).strip() if setor else None

def dedup_plantao(plantoes):
    seen = set()
    result = []
    for p in plantoes:
        key = (p["data"], p["turno"], p["inicio"], p["fim"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

def extrair_setor_e_unidade_matricial(text, lines, table_data=None):
    text_normalized = text.upper().replace('Ç', 'C').replace('Á', 'A').replace('É', 'E').replace('Í', 'I').replace('Ó', 'O').replace('Ú', 'U')
    nome_unidade, nome_setor = "NÃO INFORMADO", "NÃO INFORMADO"
    UNIDADE_ABREVIACOES = {"HMINSN": "HOSPITAL MATERNO INFANTIL NOSSA SENHORA DE NAZARETH"}
    pattern_unidade = r'UNIDADE:\s*([^\n]*)'
    pattern_setor = r'UNIDADE/SETOR:\s*([^(\n]*)'

    for line in lines:
        line_normalized = line.upper().replace('Ç', 'C').replace('Á', 'A').replace('É', 'E').replace('Í', 'I').replace('Ó', 'O').replace('Ú', 'U')
        unidade_match = re.search(pattern_unidade, line_normalized, re.I)
        if unidade_match: nome_unidade = unidade_match.group(1).strip()
        setor_match = re.search(pattern_setor, line_normalized, re.I)
        if setor_match: nome_setor = setor_match.group(1).strip()

    if nome_unidade in UNIDADE_ABREVIACOES:
        nome_unidade = UNIDADE_ABREVIACOES[nome_unidade]

    return nome_unidade, nome_setor

# --- ENDPOINTS DA API ---

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
            if not rows: continue
            headers = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
            data = [dict(zip(headers, row)) for row in rows[1:]]
            all_data[sheet_name] = data
        
        os.remove(tmp_path)
        return JSONResponse(content=all_data)
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)

@app.post("/split-pdf")
async def split_pdf(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        pages_b64 = []
        with fitz.open(stream=contents, filetype="pdf") as doc:
            for i in range(len(doc)):
                single_page = fitz.open()
                single_page.insert_pdf(doc, from_page=i, to_page=i)
                b64_bytes = single_page.write()
                b64_content = base64.b64encode(b64_bytes).decode("utf-8")
                pages_b64.append({
                    "page": i + 1,
                    "file_base64": b64_content,
                    "filename": f"page_{i+1}.pdf"
                })
                single_page.close()
        return JSONResponse(content={"pages": pages_b64})
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)

@app.post("/split-pdf-base64")
async def split_pdf_base64(request: Request):
    try:
        body = await request.json()
        b64 = body.get("base64")
        if not b64:
            return JSONResponse(content={"error": "Campo 'base64' ausente"}, status_code=400)
        
        return await split_pdf(UploadFile(file=io.BytesIO(base64.b64decode(b64))))
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)

@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        clean_text = " ".join(raw_text.replace("\r", "").split())
        lines = [clean_text[i:i+120] for i in range(0, len(clean_text), 120)]

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        for line in lines:
            pdf.multi_cell(w=190, h=8, txt=line)

        pdf_bytes = pdf.output(dest='S').encode('latin1')
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)

# --- INÍCIO normaliza-escala-from-pdf ---
@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        pages = body["pages"] if isinstance(body, dict) else body
        all_table_rows, last_unidade, last_setor, last_mes, last_ano = [], None, None, None, None

        for page_data in pages:
            b64_data = page_data.get("file_base64") or page_data.get("base64")
            if not b64_data: continue

            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page_text = doc[0].get_text("text")
                unidade, setor = extrair_metadados_pagina(page_text)
                if unidade: last_unidade = unidade
                if setor: last_setor = setor
                mes, ano = parse_mes_ano_geral(page_text)
                if mes: last_mes = mes
                if ano: last_ano = ano
                tabelas = [t.extract() for t in doc[0].find_tables() if t.extract()]
                if tabelas: all_table_rows.extend(tabelas[0])
        
        header_row_idx = next((i for i, r in enumerate(all_table_rows) if r and "NOME COMPLETO" in ''.join(map(str, r)).upper()), None)
        if header_row_idx is None:
            return JSONResponse({"error": "Cabeçalho não encontrado."}, status_code=400)
        
        header = all_table_rows[header_row_idx]
        dias_row = all_table_rows[header_row_idx + 1]
        header_map = {i: col.strip() for i, col in enumerate(dias_row) if str(col).strip().isdigit()}
        nome_idx = next(i for i, col in enumerate(header) if "NOME COMPLETO" in str(col).upper())

        profissionais_data = defaultdict(lambda: defaultdict(list))
        for row in all_table_rows[header_row_idx+2:]:
            nome_bruto = row[nome_idx]
            if not is_valid_professional_name(nome_bruto): continue
            nome = ' '.join(nome_bruto.split())
            for idx, dia in header_map.items():
                if idx < len(row) and row[idx]:
                    for turno in interpretar_turno_pdf(row[idx]):
                        horarios = HORARIOS_TURNO.get(turno)
                        dia_plantao = datetime(last_ano, last_mes, int(dia))
                        if turno == "NOITE (fim)": dia_plantao += timedelta(days=1)
                        profissionais_data[nome]["plantoes"].append({
                            "dia": dia_plantao.day, "data": dia_plantao.strftime('%d/%m/%Y'),
                            "turno": turno, "inicio": horarios["inicio"], "fim": horarios["fim"]
                        })

        lista_profissionais_final = [
            {"medico_nome": nome, "medico_setor": last_setor or "NÃO INFORMADO",
             "plantoes": sorted(plantoes["plantoes"], key=lambda x: (datetime.strptime(x["data"], '%d/%m/%Y'), x["inicio"]))
            } for nome, plantoes in profissionais_data.items()
        ]
        
        mes_nome_str = next(k for k, v in MONTH_MAP.items() if v == last_mes)
        return JSONResponse([{"unidade_escala": last_unidade or "NÃO INFORMADO", "mes_ano_escala": f"{mes_nome_str}/{last_ano}", "profissionais": lista_profissionais_final}])
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)
# --- FIM normaliza-escala-from-pdf ---

# --- INÍCIO normaliza-escala-PACS ---
@app.post("/normaliza-escala-PACS")
async def normaliza_escala_PACS(request: Request):
    try:
        body = await request.json()
        all_table_rows, full_text = [], ""
        last_unidade, last_setor, last_mes, last_ano = None, None, None, None

        for page_data in body:
            b64_data = page_data.get("base64") or page_data.get("bae64")
            if not b64_data: continue
            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                if not full_text: full_text = page.get_text("text")
                for table in page.find_tables():
                    if table.extract(): all_table_rows.extend(table.extract())

        unidade_match = re.search(r'UNIDADE:\s*(.*?)\n', full_text, re.I)
        setor_match = re.search(r'SETOR:\s*(.*?)\n', full_text, re.I)
        last_unidade = unidade_match.group(1).strip() if unidade_match else "NÃO INFORMADO"
        last_setor = setor_match.group(1).strip() if setor_match else "NÃO INFORMADO"
        last_mes, last_ano = parse_mes_ano_geral(full_text)

        if not all_table_rows or not last_mes or not last_ano:
            return JSONResponse({"error": "Dados insuficientes (tabela, mês ou ano) não encontrados."}, status_code=400)

        header_map, nome_idx, last_name = None, None, None
        profissionais_data = defaultdict(lambda: {"info_rows": []})

        for row in all_table_rows:
            if not row or not any(row): continue
            
            if any("NOME" in str(c or '').upper() and "COMPLETO" in str(c or '').upper() for c in row):
                header_map = {}
                offset = 1 if str(row[0]).strip().isdigit() else 0
                for i, col in enumerate(row[offset:]):
                    col_upper = str(col or '').strip().upper()
                    pos = i + offset
                    if "NOME COMPLETO" in col_upper: header_map["NOME COMPLETO"] = pos
                    elif "CARGO" in col_upper: header_map["CARGO"] = pos
                    elif "VÍNCULO" in col_upper or "VINCULO" in col_upper: header_map["VÍNCULO"] = pos
                    elif "CONSELHO" in col_upper or "CRM" in col_upper: header_map["CRM"] = pos
                    elif re.match(r'^(\d{1,2})(?:\D|$)', str(col or '').strip()):
                        day = int(re.match(r'^(\d{1,2})', str(col or '').strip()).group(1))
                        if 1 <= day <= 31: header_map[day] = pos
                nome_idx = header_map.get("NOME COMPLETO")
                continue

            if not header_map or nome_idx is None: continue
            
            nome_bruto = row[nome_idx] if nome_idx < len(row) else None
            if nome_bruto and is_valid_professional_name(nome_bruto):
                last_name = ' '.join(nome_bruto.split())
            elif nome_bruto and last_name and len(nome_bruto.strip().split()) == 1:
                last_name += f" {nome_bruto.strip()}"
            
            if last_name:
                profissionais_data[last_name]["info_rows"].append(list(row))
        
        lista_profissionais_final = []
        for nome, data in profissionais_data.items():
            primeira_linha = data["info_rows"][0]
            get_cell = lambda n, d="N/I": str(primeira_linha[header_map[n]]).strip() if header_map.get(n) and header_map[n] < len(primeira_linha) and primeira_linha[header_map[n]] else d
            
            profissional_obj = {"medico_nome": nome, "medico_crm": get_cell("CRM"), "medico_especialidade": get_cell("CARGO"), "medico_vinculo": get_cell("VÍNCULO"), "medico_setor": last_setor, "medico_unidade": last_unidade, "plantoes": []}
            if "PAES" not in profissional_obj["medico_vinculo"].upper(): continue
            
            plantoes_brutos = defaultdict(list)
            for row in data["info_rows"]:
                for dia, col_idx in header_map.items():
                    if isinstance(dia, int) and col_idx < len(row) and row[col_idx]:
                        plantoes_brutos[dia].append(str(row[col_idx]).strip())
            
            for dia, tokens in sorted(plantoes_brutos.items()):
                for token in tokens:
                    for turno_info in interpretar_turno_pacs(token):
                        horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                        data_inicio = datetime(last_ano, last_mes, dia)
                        if turno_info["turno"] == "NOITE (fim)": data_inicio += timedelta(days=1)
                        profissional_obj["plantoes"].append({
                            "data": data_inicio.strftime('%d/%m/%Y'), "dia": data_inicio.day, "turno": turno_info["turno"],
                            "setor": last_setor, "inicio": horarios.get("inicio"), "fim": horarios.get("fim")
                        })
            
            profissional_obj["plantoes"] = dedup_plantao(profissional_obj["plantoes"])
            if profissional_obj["plantoes"]:
                profissional_obj["plantoes"].sort(key=lambda p: (datetime.strptime(p['data'], '%d/%m/%Y'), p["inicio"] or ""))
                lista_profissionais_final.append(profissional_obj)
        
        lista_profissionais_final.sort(key=lambda p: p['medico_nome'])
        mes_nome_str = next(k for k, v in MONTH_MAP.items() if v == last_mes)
        final_output = [{"unidade_escala": last_unidade, "mes_ano_escala": f"{mes_nome_str}/{last_ano}", "profissionais": lista_profissionais_final}]

        return JSONResponse(content=final_output)
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
# --- FIM normaliza-escala-PACS ---

# --- INÍCIO normaliza-ESCALA-MATRIZ ---
@app.post("/normaliza-escala-MATERNIDADE-MATRICIAL")
async def normaliza_escala_maternidade_matricial(request: Request):
    try:
        body = await request.json()
        todos_profissionais = []

        if isinstance(body, list):
            for item_idx, item in enumerate(body):
                b64_list = item.get("data", []) if isinstance(item.get("data"), list) else [item]
                for page_idx, page_data in enumerate(b64_list):
                    b64 = page_data.get("base64") or page_data.get("bae64")
                    if not b64: continue
                    
                    pdf_bytes = base64.b64decode(b64)
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        page = pdf.pages[0]
                        text = page.extract_text() or ""
                        lines = [l for l in text.splitlines() if not l.strip().startswith("Governo do Estado")]
                        text = '\n'.join(lines)
                        
                        tables = page.extract_tables()
                        nome_unidade, nome_setor = extrair_setor_e_unidade_matricial(text, lines, tables[0] if tables else None)
                        mes, ano = parse_mes_ano_geral(text)
                        if not mes or not ano: continue
                        
                        if tables:
                            for table in tables:
                                header, data_started = {}, False
                                for row in table:
                                    if not data_started and any("NOME" in str(c or '').upper() for c in row):
                                        for i, col in enumerate(row):
                                            col_clean = (col or "").strip().upper()
                                            if "NOME" in col_clean: header["nome"] = i
                                            elif "VÍNCULO" in col_clean or "VINCULO" in col_clean: header["vinculo"] = i
                                            elif re.fullmatch(r"\d{1,2}", col_clean): header[int(col_clean)] = i
                                        data_started = True
                                        continue
                                    if not data_started or not row or header.get("nome") is None or not row[header["nome"]]: continue

                                    nome = ' '.join(str(row[header["nome"]]).split())
                                    vinculo = str(row[header.get("vinculo", -1)] or "").strip()
                                    if "PAES" not in vinculo.upper(): continue

                                    plantoes = []
                                    for dia, idx in header.items():
                                        if not isinstance(dia, int) or idx >= len(row) or not row[idx]: continue
                                        for turno in interpretar_turno_matricial(str(row[idx])):
                                            data_plantao = datetime(ano, mes, dia)
                                            if turno["turno"] == "NOITE (fim)": data_plantao += timedelta(days=1)
                                            horario = HORARIOS_TURNO[turno["turno"]]
                                            plantoes.append({"dia": data_plantao.day, "data": data_plantao.strftime("%d/%m/%Y"), "turno": turno["turno"], "inicio": horario["inicio"], "fim": horario["fim"], "setor": nome_setor})
                                    
                                    if plantoes:
                                        todos_profissionais.append({"medico_nome": nome, "medico_vinculo": vinculo, "medico_setor": nome_setor, "medico_unidade": nome_unidade, "plantoes": dedup_plantao(plantoes)})
        
        medicos_consolidados = defaultdict(list)
        for prof in todos_profissionais: medicos_consolidados[prof["medico_nome"]].append(prof)
        
        profissionais_final = []
        for nome, escalas in medicos_consolidados.items():
            if len(escalas) == 1:
                profissionais_final.append(escalas[0])
            else:
                base_prof = escalas[0].copy()
                all_plantoes = []
                for esc in escalas: all_plantoes.extend(esc['plantoes'])
                base_prof['plantoes'] = sorted(dedup_plantao(all_plantoes), key=lambda p: (datetime.strptime(p['data'], '%d/%m/%Y'), p['inicio']))
                profissionais_final.append(base_prof)
        
        profissionais_final.sort(key=lambda p: p["medico_nome"])
        
        # Inferir mês/ano da primeira escala encontrada
        mes_nome_str, ano_final = "INDEFINIDO", datetime.now().year
        if todos_profissionais and todos_profissionais[0]['plantoes']:
            dt_obj = datetime.strptime(todos_profissionais[0]['plantoes'][0]['data'], '%d/%m/%Y')
            ano_final = dt_obj.year
            mes_nome_str = next(k for k, v in MONTH_MAP.items() if v == dt_obj.month)
            
        return JSONResponse(content=[{"unidade_escala": "MISTA", "mes_ano_escala": f"{mes_nome_str}/{ano_final}", "profissionais": profissionais_final}])
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
# --- FIM normaliza-ESCALA-MATRIZ ---

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
