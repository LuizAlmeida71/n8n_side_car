# main.py
from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from fastapi import Body
from typing import List
from fastapi.responses import JSONResponse
from openpyxl import load_workbook
import tempfile
import uvicorn
import fitz
import base64
import os
import re

app = FastAPI()

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

# --- Validação formal via Pydantic ---
class Page(BaseModel):
    page: int
    file_base64: str

class PagesPayload(BaseModel):
    pages: List[Page]

@app.post("/normaliza-pdf")
async def normaliza_pdf(pages_payload: PagesPayload):
    try:
        textos_por_pagina = []
        for page in pages_payload.pages:
            file_data = base64.b64decode(page.file_base64)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                tmp_pdf.write(file_data)
                tmp_pdf_path = tmp_pdf.name

            with fitz.open(tmp_pdf_path) as doc:
                texto = doc[0].get_text()
                textos_por_pagina.append(texto)

        texto_completo = "\n".join(textos_por_pagina)

        resultado_normalizado = {
            "unidade_escala": "HOSPITAL EXEMPLO",
            "mes_ano_escala": "JULHO/2025",
            "profissionais": []
        }

        # Exemplo fictício de extração (substituir por lógica real):
        for match in re.finditer(r"(?P<nome>[\w\s]+)\s+RP\.PAES.*\n(?P<linha>.+)", texto_completo):
            nome = match.group("nome").strip()
            linha = match.group("linha")
            resultado_normalizado["profissionais"].append({
                "medico_nome": nome,
                "medico_crm": "",
                "medico_especialidade": "ESPECIALISTA",
                "medico_vinculo": "RP.PAES",
                "medico_setor": "Setor Exemplo",
                "plantoes": [
                    {
                        "dia": 10,
                        "data": "10/07/2025",
                        "turno": "NOITE",
                        "inicio": "19:00",
                        "fim": "07:00"
                    }
                ]
            })

        return JSONResponse(content=resultado_normalizado)

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# Para rodar localmente:
# if __name__ == "__main__":
#     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
