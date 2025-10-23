import os, time, threading
from pathlib import Path
from typing import Set, Tuple
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from backend import build_payload, emit


FRAMES_DIR = Path(os.getenv("FRAMES_DIR", "/frames"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://webhook:9001/alpr")
BACKEND = os.getenv("BACKEND", "mock").lower()
DEFAULT_REGION = os.getenv("DEFAULT_REGION", "us-tx").lower()
FILE_GLOB = [g.strip().lower() for g in os.getenv("FILE_GLOB", "*.jpg,*.jpeg,*.png").split(",")]
DEBOUNCE_MS = int(os.getenv("DEBOUNCE_MS", "400"))
RESCAN_SECONDS = int(os.getenv("RESCAN_SECONDS", "3"))

PROCESSED_DIR = FRAMES_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

print(f"[frames-watcher] watching: {FRAMES_DIR} | backend={BACKEND} | webhook={WEBHOOK_URL} | patterns={FILE_GLOB}", flush=True)

# Deduplicação e coordenação
_processed_keys: Set[Tuple[str,int,float]] = set()
_in_progress: Set[str] = set()
_lock = threading.Lock()

def _is_target(p: Path) -> bool:
    name = p.name.lower()
    return any(name.endswith(g.strip("*")) for g in FILE_GLOB)

def _file_key(p: Path) -> Tuple[str,int,float]:
    st = p.stat()
    return (p.name, st.st_size, round(st.st_mtime, 3))

def _try_start(name: str) -> bool:
    with _lock:
        if name in _in_progress:
            return False
        _in_progress.add(name)
        return True

def _finish(name: str) -> None:
    with _lock:
        _in_progress.discard(name)

def _mark_processed(k) -> None:
    with _lock:
        _processed_keys.add(k)

def _already_processed(k) -> bool:
    with _lock:
        return k in _processed_keys

def handle_file(p: Path):
    """
    Processa um único arquivo com debounce, dedupe e move para processed/.
    Protegido por in_progress para evitar corrida com re-scan/evento.
    """
    name = p.name
    try:
        # aguarda escrita finalizar
        time.sleep(DEBOUNCE_MS / 1000.0)

        if not p.exists():
            return

        k = _file_key(p)
        if _already_processed(k):
            return

        # lê bytes (pode falhar se algo remover o arquivo entre exists() e read)
        try:
            img_bytes = p.read_bytes()
        except FileNotFoundError:
            return

        payload = build_payload(img_bytes, backend=BACKEND, default_region=DEFAULT_REGION)
        emit(payload, source_file=str(name))


        print(f"[ok] {name} -> webhook", flush=True)

        # move p/ processed/ (preferir rename atômico)
        target = PROCESSED_DIR / name
        try:
            p.replace(target)
        except Exception:
            # fallback (cópia + remoção) — em bind mounts no Windows o rename pode falhar
            try:
                target.write_bytes(img_bytes)
                if p.exists():
                    p.unlink(missing_ok=True)
            except Exception as e:
                print(f"[warn] move fallback falhou para {name}: {e}", flush=True)

    except Exception as e:
        print(f"[fail] {name}: {e}", flush=True)
    finally:
        _finish(name)

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if not _is_target(p):
            return
        # calcula chave; se já processado, sai
        try:
            if p.exists():
                k = _file_key(p)
                if _already_processed(k):
                    return
        except Exception:
            pass
        # tenta marcar como em progresso (evita threads duplicadas)
        if _try_start(p.name):
            threading.Thread(target=handle_file, args=(p,), daemon=True).start()

def _scan_once():
    for g in FILE_GLOB:
        for p in FRAMES_DIR.glob(g):
            # se já em progresso, pula
            with _lock:
                if p.name in _in_progress:
                    continue
            # se já processado (mesmo conteúdo), pula
            try:
                k = _file_key(p)
                if _already_processed(k):
                    continue
            except Exception:
                # se não dá pra formar chave (arquivo sumiu), pula
                continue
            if _try_start(p.name):
                threading.Thread(target=handle_file, args=(p,), daemon=True).start()

def periodic_rescan():
    while True:
        try:
            _scan_once()
        except Exception as e:
            print(f"[rescan] erro: {e}", flush=True)
        time.sleep(RESCAN_SECONDS)

if __name__ == "__main__":
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    # varredura inicial
    _scan_once()

    # observador em modo polling (mais confiável com bind mount no Windows/macOS)
    obs = PollingObserver(timeout=RESCAN_SECONDS)
    obs.schedule(Handler(), str(FRAMES_DIR), recursive=False)
    obs.start()

    # revarredura periódica (belt & suspenders)
    threading.Thread(target=periodic_rescan, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()
