from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from openpyxl import Workbook, load_workbook
import tempfile
import fitz  # PyMuPDF
import base64
import os
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
            page_path = f"/tmp/page_{i+1}.pdf"
            single_page.save(page_path)

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


@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=12)

        for line in text.split("\n"):
            # ⚠️ Corrige erro "not enough horizontal space..."
            if len(line) > 120:
                for subline in [line[i:i+120] for i in range(0, len(line), 120)]:
                    pdf.multi_cell(0, 10, txt=subline)
            else:
                pdf.multi_cell(0, 10, txt=line)

        pdf_bytes = pdf.output(dest='S').encode("latin1")
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

