from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import pandas as pd
import io
import re
import openpyxl

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/normalize-xlsx")
async def normalize(file: UploadFile):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Arquivo deve ser .xlsx")

    try:
        buf = io.BytesIO(await file.read())
        wb = openpyxl.load_workbook(buf, data_only=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar arquivo: {str(e)}")

    frames = []

    for ws in wb.worksheets:
        try:
            ws.unmerge_cells()
            df = pd.DataFrame(ws.values)

            meta = {'unidade': None, 'setor': None, 'mes': None, 'ano': None}
            for _, row in df.iterrows():
                txt = " ".join(str(x) for x in row if x).upper()
                if "UNIDADE" in txt and not meta['unidade']:
                    meta['unidade'] = txt.split(":",1)[1].strip()
                if "SETOR" in txt and not meta['setor']:
                    meta['setor'] = txt.split(":",1)[1].strip()
                if ("MÊS" in txt or "MES" in txt) and not meta['mes']:
                    ma = re.search(r'(\w+)[^\d]*(\d{4})', txt)
                    if ma:
                        meta['mes'], meta['ano'] = ma.group(1), int(ma.group(2))

            header_row = df[df.apply(lambda r: {"NOME", "CARGO"}.issubset(
                                     {str(x).upper() for x in r}), axis=1)].index

            if len(header_row):
                df.columns = df.iloc[header_row[0]]
                df = df.iloc[header_row[0]+1:]

            df = df.dropna(how="all")
            df = df[~df.iloc[:,0].astype(str).str.contains("LEGENDA", na=False)]

            ren = {"NOME COMPLETO": "nome", "NOME": "nome", "CARGO": "cargo"}
            df = df.rename(columns=ren)

            id_cols = ["nome", "cargo"]
            day_cols = [c for c in df.columns if str(c).isdigit()]
            df = df.melt(id_vars=id_cols, value_vars=day_cols,
                         var_name="dia", value_name="turno").dropna(subset=["turno"])

            frames.append(df.assign(**meta))

        except Exception as e:
            # Continua com as demais abas mesmo se uma falhar
            continue

    if not frames:
        raise HTTPException(status_code=500, detail="Nenhuma planilha válida foi processada.")

    result = pd.concat(frames, ignore_index=True)
    return JSONResponse(content=result.to_dict(orient="records"))

