from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import base64
import fitz  # PyMuPDF
import re

router = APIRouter()

# Mapeamento de setores para carimbo
SETOR_CARIMBO_MAP = {
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
    "RCP SALA DE ESTABILIZAÇÃO": "Estabilização"
}

class Pagina(BaseModel):
    page_number: int
    filename: str
    base64: str

@router.post("/classifica-paginas-hgr")
def classifica_paginas_hgr(paginas: List[Pagina]):
    resultados = []
    ultima_classificacao_valida = None
    ultima_carimbo_valido = None

    for pagina in paginas:
        try:
            binario = base64.b64decode(pagina.base64)
            doc = fitz.open("pdf", binario)
            texto = "\n".join([page.get_text() for page in doc])
        except Exception:
            texto = ""

        texto_up = texto.upper()
        classificacao = "padrao_nao_localizado"
        carimbo = None

        match = re.search(r"(UNIDADE/SETOR|SETOR)[\s:.-]*(.+)", texto_up)
        setor_extraido = None
        if match:
            setor_extraido = match.group(2).strip().splitlines()[0].strip(":-• ")

        if setor_extraido:
            for chave in SETOR_CARIMBO_MAP:
                if chave.upper() in setor_extraido:
                    classificacao = SETOR_CARIMBO_MAP[chave]
                    carimbo = SETOR_CARIMBO_MAP[chave]
                    ultima_classificacao_valida = classificacao
                    ultima_carimbo_valido = carimbo
                    break
            else:
                classificacao = "desconhecida"
                carimbo = None

        else:
            if "RETIFICAÇÃO" in texto_up or "ALTERAÇÃO" in texto_up:
                classificacao = "retificada"
                carimbo = ultima_carimbo_valido
                for j in range(len(resultados) - 1, -1, -1):
                    if resultados[j]["classificacao"] not in ["descartada", "retificada"]:
                        resultados[j]["classificacao"] = "descartada"
                        break

            elif re.search(r"(PSS|CH|PJ|M|T|N|D)", texto_up) and re.search(r"[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}", texto):
                if ultima_classificacao_valida:
                    classificacao = ultima_classificacao_valida
                    carimbo = ultima_carimbo_valido
                else:
                    classificacao = "desconhecida"
                    carimbo = None
            else:
                classificacao = "descartada"
                carimbo = ultima_carimbo_valido

        resultados.append({
            "page_number": pagina.page_number,
            "filename": pagina.filename,
            "base64": pagina.base64,
            "classificacao": classificacao,
            "carimbo": carimbo
        })

    return resultados
