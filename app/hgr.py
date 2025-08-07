from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import traceback

router = APIRouter()

@router.post("/normaliza-escala-HGR")
async def normaliza_escala_hgr(request: Request):
    try:
        body = await request.json()
        # lógica de processamento HGR aqui
        return JSONResponse(content={"status": "ok", "origem": "HGR"})
    except Exception as e:
        return JSONResponse(content={"error": str(e), "trace": traceback.format_exc()}, status_code=500)
