from fastapi import FastAPI, UploadFile
import pandas as pd, io, re, openpyxl, os

app = FastAPI()

@app.get("/health")
def health():
    return "OK"

@app.post("/normalize-xlsx")
async def normalize(file: UploadFile):
    buf = io.BytesIO(await file.read())
    wb = openpyxl.load_workbook(buf, data_only=True)

    frames = []
    for ws in wb.worksheets:
        ws.unmerge_cells()                    # 1. remove merges
        df = pd.DataFrame(ws.values)

        # 2. extrai metadados
        meta = {'unidade': None, 'setor': None, 'mes': None, 'ano': None}
        for _, row in df.iterrows():
            txt = " ".join(str(x) for x in row if x).upper()
            if "UNIDADE" in txt:
                meta['unidade'] = txt.split(":",1)[1].strip()
            if "SETOR" in txt:
                meta['setor'] = txt.split(":",1)[1].strip()
            if "MÊS" in txt or "MES" in txt:
                ma = re.search(r'(\w+)[^\d]*(\d{4})', txt)
                if ma: meta['mes'], meta['ano'] = ma.group(1), int(ma.group(2))

        # 3-4. acha cabeçalho “NOME” + “CARGO”, limpa legenda
        header_row = df[df.apply(lambda r: {"NOME","CARGO"}.issubset(
                                 {str(x).upper() for x in r}), axis=1)].index
        if len(header_row):
            df.columns = df.iloc[header_row[0]]
            df = df.iloc[header_row[0]+1:]
        df = df.dropna(how="all")                    # remove linhas vazias
        df = df[~df.iloc[:,0].astype(str).str.contains("LEGENDA", na=False)]

        # 5. renomeia, derrete dias 1-31
        ren = {"NOME COMPLETO":"nome", "NOME":"nome", "CARGO":"cargo"}
        df = df.rename(columns=ren)
        id_cols = ["nome","cargo"]
        day_cols = [c for c in df.columns if str(c).isdigit()]
        df = df.melt(id_vars=id_cols, value_vars=day_cols,
                     var_name="dia", value_name="turno").dropna(subset=["turno"])
        frames.append(df.assign(**meta))

    result = pd.concat(frames, ignore_index=True)
    # Railway às vezes limita muito output – opte por CSV se necessário
    return result.to_dict(orient="records")
