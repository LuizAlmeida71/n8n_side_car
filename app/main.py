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
    match = re.search(r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})', text.upper())
    if not match: return None, None
    mes_nome, ano_str = match.groups()
    mes = MONTH_MAP.get(mes_nome)
    ano = int(ano_str)
    return mes, ano

def interpretar_turno(token, medico_setor):
    if not token or not isinstance(token, str): return []
    token_clean = token.replace('\n', '').replace('/', '').replace(' ', '')
    tokens = list(token_clean)
    turnos_finais = []
    for t in tokens:
        if t == 'M': turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T': turnos_finais.append({"turno": "TARDE"})
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
        full_text, all_table_rows = "", []
        last_header_row = None
        last_setor = None
        last_unidade = None
        last_mes, last_ano = None, None

        for page_idx, page_data in enumerate(body):
            b64_data = page_data.get("base64")  # <- Corrigido de "bae64"
            if not b64_data: continue
            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                full_text += page_text + "\n"
                for table in page.find_tables():
                    extracted = table.extract()
                    if extracted: all_table_rows.extend(extracted)

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
                first_is_index = (not row[0] or str(row[0]).strip().isdigit())
                start = 1 if first_is_index else 0
                header_row = row[start:]
                header_map = {}
                for i, col_name in enumerate(header_row):
                    clean_name = str(col_name).replace('\n', ' ').strip().upper()
                    if "NOME COMPLETO" in clean_name: header_map["NOME COMPLETO"] = i+start
                    elif "CARGO" in clean_name: header_map["CARGO"] = i+start
                    elif "VÍNCULO" in clean_name or "VINCULO" in clean_name: header_map["VÍNCULO"] = i+start
                    elif "CONSELHO" in clean_name or "CRM" in clean_name: header_map["CRM"] = i+start
                    elif isinstance(col_name, (int, float)) or (str(col_name).strip().isdigit() if col_name else False):
                        header_map[int(str(col_name).strip())] = i+start
                nome_idx = header_map.get("NOME COMPLETO")
                last_name = None
                idx_linha += 1
                continue

            if not header_map or nome_idx is None:
                idx_linha += 1
                continue

            row = all_table_rows[idx_linha]
            if row and (not row[0] or str(row[0]).strip().isdigit()):
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

            crm_idx = header_map.get("CRM")
            cargo_idx = header_map.get("CARGO")
            vinculo_idx = header_map.get("VÍNCULO")

            profissional_obj = {
                "medico_nome": nome,
                "medico_crm": str(primeira_linha[crm_idx]).strip() if crm_idx is not None and crm_idx < len(primeira_linha) and primeira_linha[crm_idx] else "N/I",
                "medico_especialidade": str(primeira_linha[cargo_idx]).strip() if cargo_idx is not None and cargo_idx < len(primeira_linha) else "N/I",
                "medico_vinculo": str(primeira_linha[vinculo_idx]).strip() if vinculo_idx is not None and vinculo_idx < len(primeira_linha) else "N/I",
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
                    turnos = interpretar_turno(token, last_setor or "")
                    try:
                        data_plantao = datetime(last_ano, last_mes, dia)
                    except Exception:
                        continue
                    for turno_info in turnos:
                        horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                        if turno_info["turno"] == "NOITE (fim)":
                            try:
                                data_fim = data_plantao + timedelta(days=1)
                                profissional_obj["plantoes"].append({
                                    "dia": data_fim.day,
                                    "data": data_fim.strftime('%d/%m/%Y'),
                                    "turno": turno_info["turno"],
                                    "inicio": horarios.get("inicio"),
                                    "fim": horarios.get("fim")
                                })
                            except Exception:
                                continue
                        else:
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
