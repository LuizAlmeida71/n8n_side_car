from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
import re
import unicodedata
from difflib import get_close_matches

router = APIRouter()

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
    "URGÊNCIA E EMERGÊNCIA": "EU",
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
    "OBSERVAÇÃO": "Observacao",
    "ÁREA DE SUTURA": "Sutura",
    "RCP SALA DE ESTABILIZAÇÃO": "Estabilizacao"
}

SETOR_CHAVES_NORMALIZADAS = {
    re.sub(r'[^a-z0-9 ]', '', unicodedata.normalize('NFKD', k.lower()).encode('ascii', 'ignore').decode()): v
    for k, v in SETOR_CARIMBO_MAP.items()
}

class Pagina(BaseModel):
    page_number: int
    filename: str
    base64: str
    text: str

@router.post("/classifica-paginas-hgr")
def classifica_paginas_hgr(paginas: List[Pagina]):
    resultados = []
    ultima_classificacao_valida = None
    ultima_carimbo_valido = None

    for i, pagina in enumerate(paginas):
        texto = pagina.text
        texto_lower = texto.lower()
        texto_normalizado = re.sub(r'[^a-z0-9 ]', '', unicodedata.normalize('NFKD', texto_lower).encode('ascii', 'ignore').decode())
        linhas = texto_normalizado.splitlines()

        classificacao = "desconhecida"
        carimbo = None

        # Verifica cabeçalho de setor
        match = re.search(r'(unidade ?/ ?setor|setor)[\s:.-]*(.+)', texto_lower)
        setor_extraido = None
        if match:
            setor_extraido = match.group(2).strip().splitlines()[0].strip(" :-•")
            setor_normalizado = re.sub(r'[^a-z0-9 ]', '', unicodedata.normalize('NFKD', setor_extraido.lower()).encode('ascii', 'ignore').decode())
            setor_proximo = get_close_matches(setor_normalizado, SETOR_CHAVES_NORMALIZADAS.keys(), n=1, cutoff=0.75)
            if setor_proximo:
                classificacao = SETOR_CHAVES_NORMALIZADAS[setor_proximo[0]]
                carimbo = classificacao
                ultima_classificacao_valida = classificacao
                ultima_carimbo_valido = carimbo
            else:
                classificacao = "desconhecida"
                carimbo = None
        else:
            # Retificação substitui página anterior
            if "retifica" in texto_lower or "alteracao" in texto_lower:
                classificacao = "retificada"
                carimbo = ultima_carimbo_valido
                for j in range(len(resultados) - 1, -1, -1):
                    if resultados[j]["classificacao"] not in ["retificada", "descartada"]:
                        resultados[j]["classificacao"] = "descartada"
                        break
            # Verificação de lixo
            elif re.search(r"documento assinado|autenticidade do documento|decreto|sei\.rr\.gov\.br", texto_lower):
                classificacao = "descartada"
                carimbo = ultima_carimbo_valido
            # Dados úteis: nome + turno
            elif re.search(r"\b(pss1|chm|pjm|pj|m|t|n|d)\b", texto_lower) and re.search(r"\b[a-z]+ [a-z]+\b", texto_lower):
                classificacao = ultima_classificacao_valida or "desconhecida"
                carimbo = ultima_carimbo_valido
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
