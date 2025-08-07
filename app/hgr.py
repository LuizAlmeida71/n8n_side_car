from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from collections import defaultdict
from datetime import datetime, timedelta
import base64
import traceback
import io
import pdfplumber
import re

router = APIRouter()

HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 'MAIO': 5,
    'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10,
    'NOVEMBRO': 11, 'DEZEMBRO': 12
}

def interpretar_turno(token):
    if not token or not isinstance(token, str):
        return []
    token_clean = token.replace('\n', '').replace(' ', '').replace('/', '')
    if "TOTAL" in token.upper() or "PL" in token.upper():
        return []
    tokens = list(token_clean.upper())
    turnos = []
    for t in tokens:
        if t == 'M':
            turnos.append("MANHÃ")
        elif t == 'T':
            turnos.append("TARDE")
        elif t == 'D':
            turnos.extend(["MANHÃ", "TARDE"])
        elif t == 'N':
            turnos.extend(["NOITE (início)", "NOITE (fim)"])
    return turnos

def dedup_plantao(plantoes):
    seen = set()
    result = []
    for p in plantoes:
        key = (p["data"], p["turno"], p["inicio"], p["fim"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

def parse_mes_ano(text):
    text = text.upper().replace('Ç', 'C')
    regex = '|'.join(MONTH_MAP.keys())
    match = re.search(r'(?:MÊS[^A-Z]*)?(' + regex + r')[^\d]*(\d{4})', text)
    if not match:
        return None, None
    mes_nome, ano = match.groups()
    return MONTH_MAP.get(mes_nome.upper()), int(ano)

def extrair_unidade_setor(text):
    unidade = re.search(r'UNIDADE[:\s-]+(.+?)(?:SETOR|MÊS|ESCALA|$)', text, re.I)
    setor = re.search(r'SETOR[:\s-]+(.+?)(?:MÊS|ESCALA|$)', text, re.I)
    return (
        unidade.group(1).strip() if unidade else "NÃO INFORMADO",
        setor.group(1).strip() if setor else "NÃO INFORMADO"
    )

@router.post("/normaliza-escala-HGR")
async def normaliza_escala_hgr(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, list):
            return JSONResponse(content={"error": "Formato inválido. Esperado array de páginas base64."}, status_code=400)

        profissionais_map = defaultdict(lambda: {"info": {}, "plantoes": []})
        last_mes, last_ano = None, None
        unidade, setor = "NÃO INFORMADO", "NÃO INFORMADO"

        for page_data in body:
            b64 = page_data.get("base64") or page_data.get("file_base64")
            if not b64:
                continue
            pdf_bytes = base64.b64decode(b64)
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                page = pdf.pages[0]
                text = page.extract_text() or ""
                if not last_mes or not last_ano:
                    last_mes, last_ano = parse_mes_ano(text)
                unidade, setor = extrair_unidade_setor(text)

                tables = page.extract_tables()
                if not tables:
                    continue

                header_map = {}
                nome_atual = None

                for row in tables[0]:
                    if not row or not any(row):
                        continue

                    if any("NOME COMPLETO" in str(cell or "").upper() for cell in row):
                        for i, cell in enumerate(row):
                            val = str(cell or "").upper().strip()
                            if "NOME COMPLETO" in val:
                                header_map["nome"] = i
                            elif "CARGO" in val:
                                header_map["cargo"] = i
                            elif "VÍNCULO" in val or "VINCULO" in val:
                                header_map["vinculo"] = i
                            elif "CRM" in val or "CONSELHO" in val:
                                header_map["crm"] = i
                            elif re.match(r'^\d{1,2}$', val):
                                header_map[int(val)] = i
                        continue
                    
                    if "nome" not in header_map or header_map["nome"] >= len(row):
                        continue

                    nome_bruto = str(row[header_map["nome"]] or "").strip().replace("\n", " ")
                    if len(nome_bruto.split()) >= 2:
                        nome_atual = nome_bruto
                    elif nome_bruto and nome_atual:
                        nome_atual += f" {nome_bruto.strip()}"

                    if not nome_atual:
                        continue
                    
                    vinculo = str(row[header_map.get("vinculo", -1)] or "").upper()
                    if "PAES" not in vinculo:
                        continue
                    
                    cargo = str(row[header_map.get("cargo", -1)] or "").strip()
                    crm = str(row[header_map.get("crm", -1)] or "").strip()

                    if not profissionais_map[nome_atual]["info"]:
                        profissionais_map[nome_atual]["info"] = {
                            "medico_nome": nome_atual,
                            "medico_crm": crm or "",
                            "medico_especialidade": cargo or "",
                            "medico_vinculo": vinculo or "",
                            "medico_setor": setor,
                            "medico_unidade": unidade
                        }

                    for dia, idx in header_map.items():
                        if isinstance(dia, int) and idx < len(row):
                            token = str(row[idx] or "").strip()
                            for turno in interpretar_turno(token):
                                data = datetime(last_ano, last_mes, dia)
                                if turno == "NOITE (fim)":
                                    data += timedelta(days=1)
                                horarios = HORARIOS_TURNO.get(turno, {})
                                profissionais_map[nome_atual]["plantoes"].append({
                                    "data": data.strftime("%d/%m/%Y"),
                                    "dia": data.day,
                                    "turno": turno,
                                    "inicio": horarios.get("inicio"),
                                    "fim": horarios.get("fim"),
                                    "setor": setor,
                                    "medico_unidade": unidade,
                                })

        profissionais_final = []
        for nome, dados in profissionais_map.items():
            dados["plantoes"] = dedup_plantao(dados["plantoes"])
            dados["plantoes"].sort(key=lambda x: (datetime.strptime(x["data"], "%d/%m/%Y"), x["inicio"] or ""))
            profissional = dados["info"]
            profissional["plantoes"] = dados["plantoes"]
            profissionais_final.append(profissional)

        mes_nome = next((k for k, v in MONTH_MAP.items() if v == last_mes), "MÊS")
        return JSONResponse(content=[{
            "unidade_escala": unidade,
            "mes_ano_escala": f"{mes_nome}/{last_ano}",
            "profissionais": sorted(profissionais_final, key=lambda p: p["medico_nome"])
        }])
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
