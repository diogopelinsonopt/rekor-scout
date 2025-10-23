import hashlib, os, random, time, json, requests
from pathlib import Path
from typing import Dict, Any, Optional, List

def _now_ms() -> int:
    return int(time.time() * 1000)

def _plate_from_bytes(b: bytes) -> str:
    h = hashlib.sha1(b).hexdigest().upper()
    letters = ''.join([c for c in h if c.isalpha()])[:3] or "ABC"
    digits  = ''.join([c for c in h if c.isdigit()])[:4] or "1234"
    return f"{letters}{digits}"

def recognize_mock(img_bytes: bytes, *, default_region: str = "us-tx") -> Dict[str, Any]:
    plate = _plate_from_bytes(img_bytes)
    conf  = 85.0 + random.random() * 10.0
    return {
        "version": 2,
        "data_type": "alpr_results",
        "img_width": 0,
        "img_height": 0,
        "epoch_time": _now_ms(),
        "camera_id": int(os.getenv("CAMERA_ID", "1")),
        "results": [
            {
                "plate": plate,
                "confidence": round(conf, 2),
                "region": default_region,
                "region_confidence": 80.0
            }
        ],
    }

def recognize_rekor_api(img_bytes: bytes) -> Dict[str, Any]:
    url = os.getenv("REKOR_API_URL", "").strip()
    key = os.getenv("REKOR_API_KEY", "").strip()
    country = os.getenv("REKOR_COUNTRY", "us").strip()
    state_hint = os.getenv("REKOR_STATE_HINT", "").strip()
    if not url or not key:
        raise RuntimeError("REKOR_API_URL/REKOR_API_KEY não configurados")

    headers = {"Authorization": f"Key {key}"}
    files = {"image": ("frame.jpg", img_bytes, "image/jpeg")}
    data = {"country": country}
    if state_hint:
        data["state"] = state_hint

    resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
    resp.raise_for_status()
    js = resp.json()

    results = js.get("results", [])
    if results:
        best = results[0]
        plate = best.get("plate")
        region = best.get("region") or best.get("region_code") or "us-xx"
        conf = best.get("confidence") or 90.0
    else:
        plate, region, conf = None, None, 0.0

    return {
        "version": 2,
        "data_type": "alpr_results",
        "img_width": js.get("img_width", 0),
        "img_height": js.get("img_height", 0),
        "epoch_time": js.get("epoch_time", _now_ms()),
        "camera_id": int(os.getenv("CAMERA_ID", "1")),
        "results": ([{
            "plate": plate,
            "confidence": conf,
            "region": region,
            "region_confidence": js.get("region_confidence", 0.0)
        }] if plate and region else [])
    }

def build_payload(img_bytes: bytes, backend: str, default_region: str) -> Dict[str, Any]:
    if backend == "mock":
        return recognize_mock(img_bytes, default_region=default_region)
    elif backend == "rekor_api":
        return recognize_rekor_api(img_bytes)
    else:
        raise ValueError(f"BACKEND desconhecido: {backend}")

# ---------- SINKS (destinos) ----------

def simplify(payload: Dict[str, Any], source_file: Optional[str]) -> List[Dict[str, Any]]:
    """Converte 'alpr_results' -> lista [{plate, state, confidence, camera_id, epoch_time, source_file}]"""
    out: List[Dict[str, Any]] = []
    if payload.get("data_type") != "alpr_results":
        return out
    for r in payload.get("results", []):
        plate = r.get("plate")
        region = r.get("region")
        if not (plate and region):
            continue
        state = region.split("-", 1)[-1].upper() if "-" in region else region
        out.append({
            "plate": plate,
            "state": state,
            "confidence": r.get("confidence"),
            "camera_id": payload.get("camera_id"),
            "epoch_time": payload.get("epoch_time"),
            "source_file": source_file,
        })
    return out

def sink_file(payload: Dict[str, Any], source_file: Optional[str], path: str) -> None:
    """Grava cada resultado simplificado como uma linha JSON (NDJSON)"""
    rows = simplify(payload, source_file)
    if not rows:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")

def sink_webhook(payload: Dict[str, Any], webhook_url: str, source_file: Optional[str]) -> None:
    if source_file:
        payload = dict(payload)
        payload["source_file"] = source_file
    r = requests.post(webhook_url, json=payload, timeout=15)
    r.raise_for_status()

def emit(payload: Dict[str, Any], *, source_file: Optional[str]) -> None:
    """Escolhe o destino com base nas variáveis de ambiente."""
    sink = os.getenv("SINK", "file").lower()
    if sink == "file":
        sink_file(payload, source_file, os.getenv("SINK_PATH", "/frames/results.ndjson"))
    elif sink == "webhook":
        sink_webhook(payload, os.getenv("WEBHOOK_URL", "http://webhook:9001/alpr"), source_file)
    else:
        raise ValueError(f"SINK inválido: {sink}")
