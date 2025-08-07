from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import base64
import re

router = APIRouter()

# Mapeamento de Setor para Carimbo
SETOR_CARIMBO = {
    "PARECER": "Parecer",
    "HGR PRESCRIÇÃO CLINICOS DA CIRURGIA GERAL- BLOCO F": "Bloco F",
    "EMERGÊNCIA PARA TODAS UTI´S": "Emergência",
    "HOSPITALISTAS - BLOCO C, BLOCO D": "Bloco D",
    "ENFERMARIA CARDIOLOGISTA/BLOCOS": "Enfermaria",
    "UTI CARDIOLÓGICA DIA - HGR CARDIOLOGIA / CLÍNICOS": "Cardiologia",
    "NEUROCIRURGIA": "Neurocirurgia",
    "UNIDADE DE AVC": "AVC",
    "UNIDADE DE TERAPIA INTENSIVA - UTI 1": "UTI 1",
    "UNIDADE DE TERAPIA INTENSIVA - UTI 2": "UTI 2",
    "Urgência e Emergência": "EU",
    "BLOCO E - PRESCRIÇÃO": "Prescrição",
    "BLOCO E HOSPITALISTA": "Hospitalistas E",
    "HGR PRESCRIÇÃO CLINICOS DA NEUROCIRURGIA BLOCO F": "Neuro F",
    "BLOCO A HOSPITALISTA": "Hospitalistas A",
    "BLOCOS F HOSPITALISTA": "Hospitalistas F",
    "NÚCLEO INTERNO DE REGULAÇÃO - NIR": "NIR",
    "RCP - SALA DE ESTABILIZAÇÃO": "RCP",
    "CLINICO GERAL - PRONTO SOCORRO AYRTON ROCHA": "OS",
    "UTI 03": "UTI 3",
    "CONSULTORIOS": "Consultorios",
    "OBSERVAÇÃO": "Observação",
    "ÁREA DE SUTURA": "Sutura",
    "RCP SALA DE ESTABILIZAÇÃO": "Estabilização",
}

SIGLAS_VALIDAS = ["M", "T", "N", "D", "PJM", "CHM", "PSS1", "CH"]

@router.post("/classifica-paginas-hgr")
async def classifica_paginas_hgr(request: Request):
    try:
        paginas = await request.json()
        resultado = []
        ultima_classificacao_valida = None

        for idx, pagina in enumerate(paginas):
            base64_pdf = pagina.get("base64") or pagina.get("bae64")  # fallback
            filename = pagina.get("filename")
            page_number = pagina.get("page_number")

            if not base64_pdf:
                resultado.append({"page_number": page_number, "classificacao": "erro_sem_base64", "carimbo": None})
                continue

            try:
                pdf_bytes = base64.b64decode(base64_pdf)
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                texto = "\n".join(page.get_text() for page in doc)
            except Exception:
                resultado.append({"page_number": page_number, "classificacao": "erro_pdf_invalido", "carimbo": None})
                continue

            texto_normalizado = texto.upper()

            # Verifica se é retificação
            if "RETIFICAÇÃO" in texto_normalizado or "ALTERAÇÃO" in texto_normalizado:
                # Marca a anterior como descartada
                for r in reversed(resultado):
                    if r["classificacao"] not in ["descartada", "erro_pdf_invalido", "erro_sem_base64"]:
                        r["classificacao"] = "descartada"
                        break
                resultado.append({"page_number": page_number, "classificacao": "retificada", "carimbo": ultima_classificacao_valida})
                continue

            # Tenta localizar cabeçalho
            match = re.search(r"(SETOR|UNIDADE/SETOR)\s*[:\-\s]?\s*(.+)", texto, re.IGNORECASE)
            if match:
                setor_extraido = match.group(2).strip().upper()

                carimbo = None
                for padrao, nome_carimbo in SETOR_CARIMBO.items():
                    if padrao.upper() in setor_extraido:
                        carimbo = nome_carimbo
                        break

                classificacao = carimbo if carimbo else "padrao_nao_localizado"
                ultima_classificacao_valida = classificacao if carimbo else ultima_classificacao_valida

                resultado.append({
                    "page_number": page_number,
                    "classificacao": classificacao,
                    "carimbo": carimbo
                })
            else:
                # Verifica se há dados de escala (siglas, nomes, etc.)
                tem_dados = any(sigla in texto_normalizado for sigla in SIGLAS_VALIDAS)
                if tem_dados and ultima_classificacao_valida:
                    resultado.append({
                        "page_number": page_number,
                        "classificacao": ultima_classificacao_valida,
                        "carimbo": ultima_classificacao_valida
                    })
                else:
                    resultado.append({
                        "page_number": page_number,
                        "classificacao": "descartada",
                        "carimbo": None
                    })

        return JSONResponse(content=resultado)

    except Exception as e:
        import traceback
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
