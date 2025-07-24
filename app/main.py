from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from openpyxl import Workbook, load_workbook, openpyxl
import tempfile
import fitz  # PyMuPDF
import base64
import os
from fpdf import FPDF
import traceback

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

@app.post("/desfazer-merges")
async def desfazer_merges(request: Request):
    try:
        # Recebe o corpo da requisição, que é uma lista de objetos {"row": [...]}
        body = await request.json()

        # Verifica se a entrada é uma lista e não está vazia
        if not isinstance(body, list) or not body:
            return JSONResponse(
                content={"error": "A entrada deve ser uma lista de objetos não vazia."}, 
                status_code=400
            )

        # Array para armazenar o resultado final com os merges desfeitos
        output_data = []
        
        # Variável para armazenar a última linha completa vista.
        # Inicializa com o número de colunas da primeira linha para garantir consistência.
        num_colunas = len(body[0].get("row", []))
        ultima_linha_completa = [None] * num_colunas

        # Itera sobre cada item (linha) na entrada
        for item in body:
            linha_atual = item.get("row", [])
            
            # Garante que a linha tenha o número esperado de colunas, preenchendo com None se necessário
            while len(linha_atual) < num_colunas:
                linha_atual.append(None)
            
            nova_linha = []
            
            # Itera sobre cada célula da linha atual
            for i in range(num_colunas):
                # Se a célula atual não for nula, ela contém um novo valor.
                # Atualizamos a "última linha completa" e usamos esse novo valor.
                if linha_atual[i] is not None and str(linha_atual[i]).strip() != '':
                    ultima_linha_completa[i] = linha_atual[i]
                    nova_linha.append(linha_atual[i])
                # Se a célula atual for nula, usamos o valor da "última linha completa" (preenchimento).
                else:
                    nova_linha.append(ultima_linha_completa[i])
            
            # Adiciona a linha processada ao output
            output_data.append({"row": nova_linha})

        return JSONResponse(content=output_data)

    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "trace": traceback.format_exc()}, 
            status_code=500
        )
