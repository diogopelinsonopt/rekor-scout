from fastapi import FastAPI, Request
from typing import Any, Dict, List

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/alpr")
async def receive_alpr(request: Request) -> Dict[str, Any]:
    """
    Recebe JSON 'alpr_results' e retorna simplificado {plate, state}.
    """
    payload = await request.json()
    out: List[Dict[str, Any]] = []

    if payload.get("data_type") == "alpr_results":
        for r in payload.get("results", []):
            plate = r.get("plate")
            region = r.get("region")  # ex.: 'us-tx'
            if plate and region:
                state = region.split("-", 1)[-1].upper() if "-" in region else region
                out.append({
                    "plate": plate,
                    "state": state,
                    "confidence": r.get("confidence"),
                    "camera_id": payload.get("camera_id"),
                    "epoch_time": payload.get("epoch_time"),
                    "source_file": payload.get("source_file"),
                })

    print({"received": out}, flush=True)
    return {"ok": True, "count": len(out), "plates": out}
