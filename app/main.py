from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from openpyxl import Workbook, load_workbook
import tempfile
import fitz  # PyMuPDF
import base64
import os
from fpdf import FPDF
import traceback
from collections import defaultdict
import re
import traceback
from datetime import datetime, timedelta
import textwrap
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

            # Corrigido: salvar com compactação e limpeza de objetos
            page_path = f"/tmp/page_{i+1}.pdf"
            single_page.save(page_path, garbage=4, deflate=True, incremental=False)

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

# --- INÍCIO normaliza-escala-from-pdf ---
MONTH_MAP = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARÇO': 3, 'ABRIL': 4, 'MAIO': 5,
    'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8, 'SETEMBRO': 9, 'OUTUBRO': 10,
    'NOVEMBRO': 11, 'DEZEMBRO': 12
}

HORARIOS_TURNO = {
    "MANHÃ": {"inicio": "07:00", "fim": "13:00"},
    "TARDE": {"inicio": "13:00", "fim": "19:00"},
    "NOITE (início)": {"inicio": "19:00", "fim": "01:00"},
    "NOITE (fim)": {"inicio": "01:00", "fim": "07:00"},
}

def parse_mes_ano(text):
    # Busca padrões mais específicos para mês/ano
    patterns = [
        r'MÊS[\s/:]*([A-ZÇÃ]+)[\s/]*(\d{4})',
        r'([A-ZÇÃ]+)/(\d{4})',
        r'MÊS:\s*([A-ZÇÃ]+)/(\d{4})',
        r'([A-ZÇÃ]+)\s*(\d{4})'
    ]
    
    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            mes_nome, ano_str = match.groups()
            mes = MONTH_MAP.get(mes_nome)
            if mes:
                ano = int(ano_str)
                return mes, ano
    return None, None

def extract_unidade_setor_from_text(page_text):
    """
    Extrai informações de UNIDADE e SETOR do texto completo da página
    considerando diferentes formatos e merges
    """
    unidade = None
    setor = None
    
    # Padrões para UNIDADE
    unidade_patterns = [
        r'UNIDADE:\s*([^/\n]+?)(?:\s*UNIDADE[\s/_\-]*SETOR:|$|\n)',
        r'UNIDADE:\s*([^\n]+?)(?:\s{2,}|\n)',
        r'UNIDADE:\s*([^/]+?)/',
        r'UNIDADE:\s*(.+?)(?=\s*MÊS:|$|\n)'
    ]
    
    # Padrões para SETOR
    setor_patterns = [
        r'UNIDADE[\s/_\-]*SETOR:\s*([^/\n]+?)(?:\s*/\s*RESPONSÁVEL|$|\n)',
        r'UNIDADE[\s/_\-]*SETOR:\s*([^\n]+?)(?:\s{2,}|\n|$)',
        r'RESPONSÁVEL[\s/_\-]*TÉCNICO:\s*([^\n,]+?)(?:\s{2,}|\n|$)',
        r'SETOR:\s*([^/\n]+?)(?:/|$|\n)',
        r'UTI[^/\n]*([^/\n]+?)(?:/|$|\n)'
    ]
    
    for pattern in unidade_patterns:
        match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
        if match:
            unidade_raw = match.group(1).strip()
            # Remove texto desnecessário
            unidade_raw = re.sub(r'UNIDADE[\s/_\-]*SETOR.*', '', unidade_raw, flags=re.IGNORECASE)
            if unidade_raw and len(unidade_raw) > 3:
                unidade = unidade_raw
                break
    
    for pattern in setor_patterns:
        match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
        if match:
            setor_raw = match.group(1).strip()
            # Limpeza do setor
            setor_raw = re.sub(r'RESPONSÁVEL\s*TÉCNICO', '', setor_raw, flags=re.IGNORECASE)
            setor_raw = setor_raw.strip(' -:/,')
            if setor_raw and len(setor_raw) > 2:
                setor = setor_raw
                break
    
    return unidade, setor

def is_header_row(row):
    """
    Identifica se uma linha é cabeçalho da tabela
    """
    if not row:
        return False
    
    # Converte a linha para string para análise
    row_text = ' '.join([str(cell) for cell in row if cell]).upper()
    
    # Indicadores de cabeçalho
    header_indicators = [
        'NOME COMPLETO', 'NOME', 'CARGO', 'MATRÍCULA', 'VÍNCULO', 'VINCULO',
        'CRM', 'CONSELHO', 'HORÁRIO', 'HORARIO', 'CH'
    ]
    
    # Verifica se tem pelo menos 2 indicadores de cabeçalho
    count = sum(1 for indicator in header_indicators if indicator in row_text)
    
    # Ou se tem números sequenciais (dias do mês)
    numeric_count = sum(1 for cell in row if isinstance(cell, (int, float)) or 
                       (isinstance(cell, str) and cell.isdigit() and 1 <= int(cell) <= 31))
    
    return count >= 2 or numeric_count >= 5

def clean_merged_row(row, header_map):
    """
    Limpa linhas com merges, identificando valores que se estendem por múltiplas colunas
    """
    if not row or not header_map:
        return row
    
    cleaned_row = list(row)
    
    # Identifica células que podem ter sido mergidas
    for i, cell in enumerate(cleaned_row):
        if cell and isinstance(cell, str):
            # Remove quebras de linha excessivas
            cell_clean = cell.replace('\n', ' ').strip()
            # Se a célula tem muito texto, pode ser um merge
            if len(cell_clean) > 50:
                # Tenta dividir o conteúdo baseado em padrões
                parts = re.split(r'\s{3,}|\t+', cell_clean)
                if len(parts) > 1:
                    cleaned_row[i] = parts[0].strip()
                    # Tenta colocar as outras partes nas próximas colunas vazias
                    for j, part in enumerate(parts[1:], 1):
                        if i + j < len(cleaned_row) and not cleaned_row[i + j]:
                            cleaned_row[i + j] = part.strip()
            else:
                cleaned_row[i] = cell_clean
    
    return cleaned_row

def interpretar_turno(token, setor=""):
    if not token or not isinstance(token, str):
        return []
    token_clean = token.replace('\n', '').replace('/', '').replace(' ', '')
    tokens = list(token_clean)
    turnos_finais = []
    for t in tokens:
        if t == 'M':
            turnos_finais.append({"turno": "MANHÃ"})
        elif t == 'T':
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'D':
            turnos_finais.append({"turno": "MANHÃ"})
            turnos_finais.append({"turno": "TARDE"})
        elif t == 'N':
            turnos_finais.append({"turno": "NOITE (início)"})
            turnos_finais.append({"turno": "NOITE (fim)"})
        elif t == 'n':
            turnos_finais.append({"turno": "NOITE (início)"})
    return turnos_finais

def is_valid_professional_name(name):
    if not name or not isinstance(name, str): 
        return False
    name_upper = name.strip().upper()
    ignored = ["NOME COMPLETO", "LEGENDA", "ASSINATURA", "ASSINADO", "COMPLETO", 
               "CARGO", "MATRÍCULA", "HORÁRIO", "CONSELHO", "VÍNCULO"]
    if any(keyword in name_upper for keyword in ignored): 
        return False
    return len(name.split()) >= 2 or name.isupper()

def dedup_plantao(lista):
    seen = set()
    result = []
    for p in lista:
        key = (p["dia"], p["turno"], p["inicio"], p["fim"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

def build_header_map(row):
    """
    Constrói o mapeamento de colunas do cabeçalho, lidando com diferentes formatos
    """
    if not row:
        return {}, None
    
    # Remove primeira coluna se for índice numérico
    first_is_index = (not row[0] or str(row[0]).strip().isdigit() or 
                     str(row[0]).strip() in ['', 'Nº', '#', 'N°'])
    start = 1 if first_is_index else 0
    header_row = row[start:]
    
    header_map = {}
    nome_idx = None
    
    for i, col_name in enumerate(header_row):
        if not col_name:
            continue
            
        clean_name = str(col_name).replace('\n', ' ').strip().upper()
        actual_idx = i + start
        
        # Mapeamento de colunas principais
        if "NOME COMPLETO" in clean_name or clean_name == "NOME":
            header_map["NOME COMPLETO"] = actual_idx
            nome_idx = actual_idx
        elif "CARGO" in clean_name:
            header_map["CARGO"] = actual_idx
        elif "VÍNCULO" in clean_name or "VINCULO" in clean_name:
            header_map["VÍNCULO"] = actual_idx
        elif "CONSELHO" in clean_name or "CRM" in clean_name:
            header_map["CRM"] = actual_idx
        elif "MATRÍCULA" in clean_name or "MATRICULA" in clean_name:
            header_map["MATRÍCULA"] = actual_idx
        elif "HORÁRIO" in clean_name or "HORARIO" in clean_name:
            header_map["HORÁRIO"] = actual_idx
        elif clean_name == "CH":
            header_map["CH"] = actual_idx
        # Colunas numéricas (dias do mês)
        elif (isinstance(col_name, (int, float)) or 
              (isinstance(col_name, str) and col_name.isdigit())):
            day = int(col_name)
            if 1 <= day <= 31:
                header_map[day] = actual_idx
    
    return header_map, nome_idx

@app.post("/normaliza-escala-from-pdf")
async def normaliza_escala_from_pdf(request: Request):
    try:
        body = await request.json()
        all_table_rows = []
        pagina_linha_map = []
        pagina_unidade_setor_map = {}

        last_setor = None
        last_unidade = None
        last_mes = None
        last_ano = None

        for page_idx, page_data in enumerate(body):
            b64_data = page_data.get("bae64")
            if not b64_data:
                continue

            pdf_bytes = base64.b64decode(b64_data)
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page = doc[0]
                page_text = page.get_text("text")
                
                # Extração melhorada de UNIDADE e SETOR
                unidade, setor = extract_unidade_setor_from_text(page_text)
                mes, ano = parse_mes_ano(page_text)
                
                # Atualiza valores globais
                if unidade: 
                    last_unidade = unidade
                if setor: 
                    last_setor = setor
                if mes: 
                    last_mes = mes
                if ano: 
                    last_ano = ano
                
                # Mapeia informações da página
                pagina_unidade_setor_map[page_idx] = {
                    "unidade": unidade or last_unidade or "NÃO INFORMADO",
                    "setor": setor or last_setor or "NÃO INFORMADO"
                }
                
                # Extração de tabelas
                for table in page.find_tables():
                    extracted = table.extract()
                    if extracted:
                        for row in extracted:
                            all_table_rows.append(row)
                            pagina_linha_map.append(page_idx)

        if last_mes is None or last_ano is None:
            return JSONResponse(content={"error": "Mês/Ano não encontrados."}, status_code=400)

        profissionais_data = defaultdict(lambda: {"info_rows": []})
        current_header_map = None
        current_nome_idx = None
        idx_linha = 0
        last_name = None

        while idx_linha < len(all_table_rows):
            row = all_table_rows[idx_linha]
            page_idx = pagina_linha_map[idx_linha]

            # Identifica linha de cabeçalho
            if is_header_row(row):
                current_header_map, current_nome_idx = build_header_map(row)
                last_name = None
                idx_linha += 1
                continue

            # Pula linhas sem cabeçalho válido
            if not current_header_map or current_nome_idx is None:
                idx_linha += 1
                continue

            # Limpa row de merges
            row = clean_merged_row(row, current_header_map)
            
            # Remove primeira coluna se for índice
            if row and (not row[0] or str(row[0]).strip().isdigit()):
                row = row[1:]
            
            if not row or len(row) <= current_nome_idx:
                idx_linha += 1
                continue

            # Processa nome do profissional
            nome_bruto = row[current_nome_idx] if current_nome_idx < len(row) else None
            
            if nome_bruto and is_valid_professional_name(nome_bruto):
                last_name = nome_bruto.replace('\n', ' ').strip()
            elif (nome_bruto and last_name is not None and 
                  len(str(nome_bruto).strip().split()) == 1):
                last_name = f"{last_name} {str(nome_bruto).strip()}"

            # Armazena dados do profissional
            if last_name is not None:
                new_row = list(row)
                new_row[current_nome_idx] = last_name
                profissionais_data[(last_name, page_idx)]["info_rows"].append(new_row)

            idx_linha += 1

        # Processa dados finais dos profissionais
        lista_profissionais_final = []
        for (nome, page_idx), data in profissionais_data.items():
            info_rows = data["info_rows"]
            if not info_rows:
                continue
                
            primeira_linha = info_rows[0]
            unidade = pagina_unidade_setor_map.get(page_idx, {}).get("unidade", "NÃO INFORMADO")
            setor = pagina_unidade_setor_map.get(page_idx, {}).get("setor", "NÃO INFORMADO")

            # Extrai informações do profissional
            profissional_obj = {
                "medico_nome": nome,
                "medico_crm": (str(primeira_linha[current_header_map.get("CRM")]).strip() 
                              if current_header_map.get("CRM") and 
                                 current_header_map.get("CRM") < len(primeira_linha) and
                                 primeira_linha[current_header_map.get("CRM")] 
                              else "N/I"),
                "medico_especialidade": (str(primeira_linha[current_header_map.get("CARGO")]).strip() 
                                       if current_header_map.get("CARGO") and 
                                          current_header_map.get("CARGO") < len(primeira_linha)
                                       else "N/I"),
                "medico_vinculo": (str(primeira_linha[current_header_map.get("VÍNCULO")]).strip() 
                                 if current_header_map.get("VÍNCULO") and 
                                    current_header_map.get("VÍNCULO") < len(primeira_linha)
                                 else "N/I"),
                "medico_setor": setor,
                "plantoes": []
            }

            # Processa plantões
            plantoes_brutos = defaultdict(list)
            for row in info_rows:
                for dia, col_idx in current_header_map.items():
                    if isinstance(dia, int) and 1 <= dia <= 31:
                        if col_idx < len(row) and row[col_idx] and str(row[col_idx]).strip():
                            plantoes_brutos[dia].append(str(row[col_idx]).strip())

            # Converte plantões para formato final
            for dia, tokens in sorted(plantoes_brutos.items()):
                for token in tokens:
                    turnos = interpretar_turno(token, setor)
                    data_plantao = datetime(last_ano, last_mes, dia)
                    for turno_info in turnos:
                        horarios = HORARIOS_TURNO.get(turno_info["turno"], {})
                        if turno_info["turno"] == "NOITE (fim)":
                            data_fim = data_plantao + timedelta(days=1)
                            profissional_obj["plantoes"].append({
                                "dia": data_fim.day,
                                "data": data_fim.strftime('%d/%m/%Y'),
                                "turno": turno_info["turno"],
                                "inicio": horarios.get("inicio"),
                                "fim": horarios.get("fim")
                            })
                        else:
                            profissional_obj["plantoes"].append({
                                "dia": data_plantao.day,
                                "data": data_plantao.strftime('%d/%m/%Y'),
                                "turno": turno_info["turno"],
                                "inicio": horarios.get("inicio"),
                                "fim": horarios.get("fim")
                            })

            # Finaliza profissional
            profissional_obj["plantoes"] = dedup_plantao(profissional_obj["plantoes"])
            if profissional_obj["plantoes"]:
                profissional_obj["plantoes"].sort(key=lambda p: (p["dia"], p["inicio"] or ""))
                lista_profissionais_final.append(profissional_obj)

        # Resultado final
        mes_nome_str = list(MONTH_MAP.keys())[list(MONTH_MAP.values()).index(last_mes)]
        final_output = [{
            "unidade_escala": last_unidade or "NÃO INFORMADO",
            "mes_ano_escala": f"{mes_nome_str}/{last_ano}",
            "profissionais": lista_profissionais_final
        }]

        return JSONResponse(content=final_output)

    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)

# --- FIM normaliza-escala-from-pdf ---

@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        # Pré-processamento: substitui múltiplos \n e quebras "duplas"
        clean_text = raw_text.replace("\r", "").replace("\n", " ")
        clean_text = " ".join(clean_text.split())  # remove múltiplos espaços
        lines = [clean_text[i:i+120] for i in range(0, len(clean_text), 120)]

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        for line in lines:
            pdf.multi_cell(w=190, h=8, txt=line)

        # CORREÇÃO: Tratamento adequado do output do FPDF
        pdf_output = pdf.output(dest='S')
        
        # Converte para bytes se necessário
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode('latin1')
        elif isinstance(pdf_output, bytearray):
            pdf_bytes = bytes(pdf_output)
        else:
            pdf_bytes = pdf_output
        
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
# --- FIM normaliza-escala-from-pdf ---

@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        # Pré-processamento: substitui múltiplos \n e quebras "duplas"
        clean_text = raw_text.replace("\r", "").replace("\n", " ")
        clean_text = " ".join(clean_text.split())  # remove múltiplos espaços
        lines = [clean_text[i:i+120] for i in range(0, len(clean_text), 120)]

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        for line in lines:
            pdf.multi_cell(w=190, h=8, txt=line)

        # CORREÇÃO: Tratamento adequado do output do FPDF
        pdf_output = pdf.output(dest='S')
        
        # Converte para bytes se necessário
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode('latin1')
        elif isinstance(pdf_output, bytearray):
            pdf_bytes = bytes(pdf_output)
        else:
            pdf_bytes = pdf_output
        
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
# --- FIM normaliza-escala-from-pdf ---

@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        # Pré-processamento: substitui múltiplos \n e quebras "duplas"
        clean_text = raw_text.replace("\r", "").replace("\n", " ")
        clean_text = " ".join(clean_text.split())  # remove múltiplos espaços
        lines = [clean_text[i:i+120] for i in range(0, len(clean_text), 120)]

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        for line in lines:
            pdf.multi_cell(w=190, h=8, txt=line)

        # CORREÇÃO: Tratamento adequado do output do FPDF
        pdf_output = pdf.output(dest='S')
        
        # Converte para bytes se necessário
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode('latin1')
        elif isinstance(pdf_output, bytearray):
            pdf_bytes = bytes(pdf_output)
        else:
            pdf_bytes = pdf_output
        
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# --- FIM normaliza-escala-from-pdf ---

@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        raw_text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada em: {FONT_PATH}")

        # Pré-processamento: substitui múltiplos \n e quebras "duplas"
        clean_text = raw_text.replace("\r", "").replace("\n", " ")
        clean_text = " ".join(clean_text.split())  # remove múltiplos espaços
        lines = [clean_text[i:i+120] for i in range(0, len(clean_text), 120)]

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        for line in lines:
            pdf.multi_cell(w=190, h=8, txt=line)

        # CORREÇÃO: Tratamento adequado do output do FPDF
        pdf_output = pdf.output(dest='S')

        # Converte para bytes se necessário
        if isinstance(pdf_output, str):
            pdf_bytes = pdf_output.encode('latin1')
        elif isinstance(pdf_output, bytearray):
            pdf_bytes = bytes(pdf_output)
        else:
            pdf_bytes = pdf_output

        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
