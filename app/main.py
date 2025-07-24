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


@app.post("/normaliza-pdf")
async def normaliza_pdf(request: Request):
    try:
        body = await request.json()
        textos_por_pagina = []

        for page in body.get("pages", []):
            file_data = base64.b64decode(page["file_base64"])
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                tmp_pdf.write(file_data)
                tmp_pdf_path = tmp_pdf.name

            with fitz.open(tmp_pdf_path) as doc:
                texto = doc[0].get_text()
                textos_por_pagina.append(texto)

        texto_completo = "\n".join(textos_por_pagina)

        wb = Workbook()
        ws = wb.active
        ws.title = "Escala"
        ws.append(["Página", "Conteúdo"])
        for i, texto in enumerate(textos_por_pagina, 1):
            ws.append([i, texto.strip()])

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_xlsx:
            wb.save(tmp_xlsx.name)
            tmp_xlsx.seek(0)
            b64_xlsx = base64.b64encode(tmp_xlsx.read()).decode("utf-8")

        return JSONResponse(content={"file_base64": b64_xlsx, "filename": "escala_normalizada.xlsx"})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


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

# Função auxiliar para verificar se um texto é um código de plantão válido
def is_valid_shift_code(code):
    if not code or not isinstance(code, str):
        return False
    # Códigos válidos devem conter letras além de apenas D, S, T, Q (dias da semana)
    # E devem conter pelo menos uma letra que indique turno (M, T, N, D) ou tipo (CH, PJ, PSS)
    code_upper = code.upper()
    
    # Se for apenas uma letra, só pode ser D, M, T, N
    if len(code_upper.strip()) == 1:
        return code_upper.strip() in ['D', 'M', 'T', 'N']
        
    # Verifica se contém siglas de turno ou tipo
    return any(sub in code_upper for sub in ['M', 'T', 'N', 'D', 'CH', 'PJ', 'PSS'])


@app.post("/normaliza-escala-json")
async def normaliza_escala_json(request: Request):
    try:
        input_rows = await request.json()

        if not isinstance(input_rows, list) or not input_rows:
            return JSONResponse(content={"error": "A entrada deve ser uma lista de objetos 'row'."}, status_code=400)

        # --- ETAPA 1: Encontrar o cabeçalho com os dias do mês ---
        header_row = None
        header_start_index = -1
        for i, item in enumerate(input_rows):
            row = item.get("row", [])
            if "Nome Completo" in row and any(isinstance(val, int) for val in row):
                header_row = row
                header_start_index = i
                break
        
        if not header_row:
            return JSONResponse(content={"error": "Cabeçalho da escala não encontrado."}, status_code=400)

        # --- ETAPA 2: Pré-processamento - Desfazer Merges ---
        unmerged_rows = []
        last_full_row = [None] * len(header_row)
        
        # Ignora a linha de dias da semana (Q, S, T, D) que fica logo após o cabeçalho
        data_rows_start_index = header_start_index + 2 
        
        for item in input_rows[data_rows_start_index:]:
            current_row = item.get("row", [])
            if not any(current_row): continue

            processed_row = []
            for i in range(len(header_row)):
                cell_value = current_row[i] if i < len(current_row) else None
                if cell_value is not None and str(cell_value).strip() != '':
                    last_full_row[i] = cell_value
                    processed_row.append(cell_value)
                else:
                    # Propaga apenas as 7 primeiras colunas (dados do profissional)
                    if i < 7: 
                        processed_row.append(last_full_row[i])
                    else: # Mantém null para as colunas de plantão
                        processed_row.append(cell_value)
            
            unmerged_rows.append(processed_row)
            
        # --- ETAPA 3: Agrupar as linhas por nome de profissional ---
        profissionais_agrupados = defaultdict(list)
        for row in unmerged_rows:
            nome_bruto = row[0]
            if nome_bruto and isinstance(nome_bruto, str):
                nome = nome_bruto.replace('\n', ' ').strip()
                if nome:
                    profissionais_agrupados[nome].append(row)

        # --- ETAPA 4: Consolidar os dados e formatar a saída ---
        lista_profissionais_final = []
        
        for nome, linhas_do_profissional in profissionais_agrupados.items():
            primeira_linha = linhas_do_profissional[0]
            vinculos = list(set(str(linha[3] or '') for linha in linhas_do_profissional))
            
            # Q1: Estrutura agora é uma lista de turnos por dia
            plantoes_mapeados = defaultdict(list)

            for linha_dados in linhas_do_profissional:
                for i, header_col in enumerate(header_row):
                    if isinstance(header_col, int):
                        dia = str(header_col)
                        plantao_code = linha_dados[i]
                        
                        # Q2: Filtro para capturar apenas códigos de plantão válidos
                        if is_valid_shift_code(plantao_code):
                            # Adiciona à lista de plantões do dia, sem sobrescrever
                            plantoes_mapeados[dia].append(str(plantao_code).replace('\n', ' ').strip())

            if not plantoes_mapeados:
                continue

            # Remove duplicados de cada lista diária, caso haja
            for dia in plantoes_mapeados:
                plantoes_mapeados[dia] = list(set(plantoes_mapeados[dia]))

            profissional_final = {
                "medico_nome": nome,
                "cargo": str(primeira_linha[1] or '').strip(),
                "crm": str(primeira_linha[6] or '').strip(),
                "vinculos": sorted([v.strip() for v in vinculos if v.strip()]),
                "plantoes": dict(sorted(plantoes_mapeados.items(), key=lambda item: int(item[0])))
            }
            lista_profissionais_final.append(profissional_final)

        return JSONResponse(content={"profissionais": lista_profissionais_final})

    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
