from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import pandas as pd
import re

app = FastAPI()

@app.post("/normalize")
async def normalize(request: Request):
    try:
        body = await request.json()
        if "Files" not in body or not isinstance(body["Files"], list):
            raise HTTPException(status_code=400, detail="400: Campo 'Files' ausente ou inválido")

        df = pd.DataFrame(body["Files"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar JSON: {str(e)}")

    try:
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
            raise HTTPException(status_code=400, detail="Cabeçalho não encontrado.")

        df.columns = df.iloc[header_row_idx[0]]
        df = df.iloc[header_row_idx[0] + 1:]

        df = df.dropna(how="all")
        df = df[~df.iloc[:, 0].astype(str).str.contains("LEGENDA", na=False)]

        ren = {"NOME COMPLETO": "nome", "NOME": "nome", "CARGO": "cargo"}
        df = df.rename(columns=ren)

        if "nome" not in df.columns or "cargo" not in df.columns:
            raise HTTPException(status_code=400, detail="Colunas essenciais ausentes")

        day_cols = [c for c in df.columns if str(c).isdigit()]
        df = df.melt(id_vars=["nome", "cargo"], value_vars=day_cols,
                     var_name="dia", value_name="turno").dropna(subset=["turno"])

        df = df.assign(**meta)
        return JSONResponse(content=df.to_dict(orient="records"))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na normalização: {str(e)}")
