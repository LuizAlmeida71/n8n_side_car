from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import pandas as pd
import re
import base64
import io

app = FastAPI()

@app.post("/normalize")
async def normalize(request: Request):
    try:
        body = await request.json()
        files = body.get("Files")

        if not files or not isinstance(files, list):
            raise HTTPException(status_code=400, detail="Campo 'Files' ausente ou inválido")

        file_content_base64 = files[0].get("Data")
        if not file_content_base64:
            raise HTTPException(status_code=400, detail="Campo 'Data' ausente no arquivo")

        binary_data = base64.b64decode(file_content_base64)
        excel_buffer = io.BytesIO(binary_data)
        xls = pd.read_excel(excel_buffer, sheet_name=None, header=None)

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar JSON: {str(e)}")

    try:
        frames = []

        for sheet_name, df in xls.items():
            meta = {'unidade': None, 'setor': None, 'mes': None, 'ano': None}

            for _, row in df.iterrows():
                txt = " ".join(str(x) for x in row if pd.notna(x)).upper()
                if "UNIDADE" in txt and not meta['unidade']:
                    meta['unidade'] = txt.split(":", 1)[-1].strip()
                if "SETOR" in txt and not meta['setor']:
                    meta['setor'] = txt.split(":", 1)[-1].strip()
                if ("MÊS" in txt or "MES" in txt) and not meta['mes']:
                    ma = re.search(r'(\w+)[^\d]*(\d{4})', txt)
                    if ma:
                        meta['mes'], meta['ano'] = ma.group(1), int(ma.group(2))

            header_row_idx = df[df.apply(lambda r: {"NOME", "CARGO"}.issubset(
                {str(x).upper() for x in r if pd.notna(x)}), axis=1)].index

            if len(header_row_idx) == 0:
                continue

            df.columns = df.iloc[header_row_idx[0]]
            df = df.iloc[header_row_idx[0] + 1:]
            df = df.dropna(how="all")
            df = df[~df.iloc[:, 0].astype(str).str.contains("LEGENDA", na=False)]

            ren = {"NOME COMPLETO": "nome", "NOME": "nome", "CARGO": "cargo"}
            df = df.rename(columns=ren)

            if "nome" not in df.columns or "cargo" not in df.columns:
                continue

            day_cols = [c for c in df.columns if str(c).isdigit()]
            df = df.melt(id_vars=["nome", "cargo"], value_vars=day_cols,
                         var_name="dia", value_name="turno").dropna(subset=["turno"])
            frames.append(df.assign(**meta))

        if not frames:
            raise HTTPException(status_code=400, detail="Nenhuma aba processada com sucesso.")

        result = pd.concat(frames, ignore_index=True)
        return JSONResponse(content=result.to_dict(orient="records"))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na normalização: {str(e)}")
