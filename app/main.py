@app.post("/text-to-pdf")
async def text_to_pdf(request: Request):
    try:
        data = await request.json()
        text = data.get("text", "")
        filename = data.get("filename", "saida.pdf")

        if not os.path.exists(FONT_PATH):
            raise RuntimeError(f"Fonte não encontrada: {FONT_PATH}")

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_font("DejaVu", "", FONT_PATH, uni=True)
        pdf.set_font("DejaVu", size=10)

        def split_line_by_length(line, max_len=100):
            import re
            if len(line) <= max_len:
                return [line]

            # Se houver espaços, tenta quebrar por palavras
            if " " in line:
                words = line.split(" ")
                chunks, current = [], ""
                for word in words:
                    if len(current + word) + 1 <= max_len:
                        current += word + " "
                    else:
                        chunks.append(current.strip())
                        current = word + " "
                if current:
                    chunks.append(current.strip())
                return chunks
            else:
                # Palavra sem espaço: quebra bruta
                return [line[i:i+max_len] for i in range(0, len(line), max_len)]

        for line in text.splitlines():
            if not line.strip():
                pdf.ln()
            else:
                for chunk in split_line_by_length(line):
                    pdf.multi_cell(0, 5, txt=chunk)

        pdf_bytes = pdf.output(dest='S').encode("latin1")
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        return JSONResponse(content={"file_base64": base64_pdf, "filename": filename})

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

