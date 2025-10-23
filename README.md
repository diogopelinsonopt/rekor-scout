# Rekor POC — Hotfolder → JSON (e/ou Webhook)

Esta POC entrega um pipeline simples e robusto para reconhecimento de placas com **câmeras gravando frames em uma pasta**.
Ele funciona **sem licença** (modo *mock* que gera um resultado determinístico por arquivo) e já fica **100% pronto** para, quando chegar a licença, plugar o **Rekor Scout Agent** ou a **API paga** sem refatorar nada da arquitetura.

## Arquitetura (visão geral)

```
[Camera] -> grava frames em ./frames/
                 |
                 v
        frames-watcher (Python 3.11)
           - dedupe, debounce, move p/ processed/
           - gera payload "alpr_results"
           - envia p/ arquivo NDJSON (SINK=file) OU para o webhook (SINK=webhook)
                 |
                 +--> results.ndjson (SINK=file)
                 |
                 +--> webhook FastAPI -> extrai { plate, state } (SINK=webhook)
```

* **Sem licença (hoje):** `frames-watcher` usa `BACKEND=mock` e grava saídas em `frames/results.ndjson` (ou posta no webhook, você escolhe).
* **Com licença (depois):**

  * **Opção A (recomendada):** subir o **Rekor Scout Agent** (on-prem) para processar as câmeras e **POSTar** no mesmo webhook FastAPI (contrato idêntico).
  * **Opção B:** manter o `frames-watcher`, trocar `BACKEND=rekor_api` e apontar `REKOR_API_URL/REKOR_API_KEY` para o endpoint oficial que vocês adquirirem.

---

## Estrutura de pastas

```
rekor-poc/
  docker-compose.yml
  .env                         # (opcional) variáveis de ambiente
  frames/
    .gitkeep
    processed/                 # onde os frames processados são movidos
    results.ndjson             # saída quando SINK=file
  webhook/
    app.py
    requirements.txt
    Dockerfile
  frames-watcher/
    watcher.py
    backend.py
    requirements.txt
    Dockerfile
    agent/
        alprd.conf
        license.conf
```

---

## Serviços

### 1) frames-watcher

* **O que faz:** monitora `./frames/`, processa **novos** arquivos (`.jpg/.jpeg/.png`), gera um JSON no formato **`alpr_results`** e:

  * grava as saídas simplificadas em `results.ndjson` (**SINK=file**, padrão), **ou**
  * envia o payload bruto para o **webhook** (**SINK=webhook**).
* **Extras técnicos:**

  * Evita duplicidade via *locks* e *keys* (nome, tamanho, mtime).
  * *Debounce* para aguardar escrita do arquivo.
  * Move os frames **já processados** para `frames/processed/`.
  * Usa **PollingObserver** + revarredura periódica (confiável em Windows/Docker Desktop).

### 2) webhook (FastAPI)

* **O que faz:** recebe `alpr_results` (como o Agent oficial enviará) e **extrai** `{ plate, state }`.
* **Uso:** útil em **produção** com o **Agent**; no *mock*, você pode usar só o `SINK=file` e nem subir o webhook.

---

## Variáveis de ambiente (as principais)

| Variável           | Onde           | Padrão                     | Descrição                                                         |
| ------------------ | -------------- | -------------------------- | ----------------------------------------------------------------- |
| `SINK`             | frames-watcher | `file`                     | `file` → grava NDJSON em `SINK_PATH`; `webhook` → POST no webhook |
| `SINK_PATH`        | frames-watcher | `/frames/results.ndjson`   | Caminho do NDJSON quando `SINK=file`                              |
| `WEBHOOK_URL`      | frames-watcher | `http://webhook:9001/alpr` | Endpoint do webhook quando `SINK=webhook`                         |
| `BACKEND`          | frames-watcher | `mock`                     | `mock` hoje; depois `rekor_api`                                   |
| `REKOR_API_URL`    | frames-watcher | *(vazio)*                  | URL da API paga (quando tiver)                                    |
| `REKOR_API_KEY`    | frames-watcher | *(vazio)*                  | Chave da API paga                                                 |
| `REKOR_COUNTRY`    | frames-watcher | `us`                       | Hint para backend pago                                            |
| `REKOR_STATE_HINT` | frames-watcher | *(vazio)*                  | Hint de estado opcional (backend pago)                            |
| `DEFAULT_REGION`   | frames-watcher | `us-tx`                    | Região usada no mock (vira `state=TX`)                            |
| `CAMERA_ID`        | frames-watcher | `1`                        | Identificador lógico da câmera                                    |
| `FILE_GLOB`        | frames-watcher | `*.jpg,*.jpeg,*.png`       | Extensões monitoradas                                             |
| `DEBOUNCE_MS`      | frames-watcher | `400`                      | Espera antes de ler o arquivo                                     |
| `RESCAN_SECONDS`   | frames-watcher | `2`–`3`                    | Revarredura periódica                                             |

> Você pode colocar isso num `.env` e referenciar no `docker-compose.yml` com `env_file: ['.env']`.

---

## Como rodar (sem licença, **SINK=file**)

> Este é o modo de **apresentação**: não exige webhook nem acesso a APIs externas.

1. **Build & Up**

```bash
docker compose build
docker compose up -d frames-watcher
```

2. **Copiar um frame para processar**

```powershell
# Windows PowerShell
Copy-Item .\alguma_placa.jpg .\frames\
```

3. **Ver os logs**

```bash
docker compose logs -f frames-watcher
# [ok] alguma_placa.jpg -> webhook   (a mensagem diz "webhook" mas a saída vai para arquivo quando SINK=file)
```

4. **Ver o resultado (NDJSON)**

```powershell
type .\frames\results.ndjson        # Windows
# ou
tail -n 5 frames/results.ndjson     # Linux/macOS
```

Cada linha é um JSON simplificado:

```json
{"plate":"ABC1234","state":"TX","confidence":92.1,"camera_id":1,"epoch_time":17612xxxxx,"source_file":"alguma_placa.jpg"}
```

> Os arquivos originais serão movidos para `frames/processed/` após o processamento.

---

## Como rodar (sem licença, **SINK=webhook**) — opcional

Se quiser ver o **webhook** funcionando com o payload `alpr_results`:

1. No `docker-compose.yml`, deixe:

```yaml
frames-watcher:
  environment:
    - SINK=webhook
    - WEBHOOK_URL=http://webhook:9001/alpr
# ...
webhook:
  build: ./webhook
  ports:
    - "9001:9001"
```

2. Suba:

```bash
docker compose up -d webhook frames-watcher
docker compose logs -f webhook
```

3. Copie um frame para `frames/` e observe o webhook logar:

```
{'received': [{'plate': 'ABC1234', 'state': 'TX', 'confidence': 9x.x, 'camera_id': 1, 'epoch_time': ..., 'source_file': 'alguma_placa.jpg'}]}
```

---

## Quando a licença chegar (dois caminhos)

### Opção A — **Rekor Scout Agent** (recomendado)

1. Crie `agent/` com:

   * `alprd.conf` (mínimo):

     ```ini
     upload_data = 1
     upload_address = http://webhook:9001/alpr
     web_server_enabled = 1
     websockets_enabled = 0
     # configure cameras (RTSP) aqui ou via web server do Agent
     ```
   * `license.conf` com a sua **chave on-prem**.
2. Adicione o serviço no `docker-compose.yml`:

   ```yaml
   rekor-agent:
     image: openalpr/agent:4.1.10
     container_name: rekor_agent
     depends_on:
       - webhook
     volumes:
       - ./agent/alprd.conf:/etc/openalpr/alprd.conf:ro
       - ./agent/license.conf:/etc/openalpr/license.conf:ro
     restart: unless-stopped
   ```
3. Suba:

   ```bash
   docker compose up -d rekor-agent
   docker compose logs -f rekor-agent
   ```
4. **O que muda:** o Agent passa a **POSTar automaticamente** no webhook, no mesmo formato `alpr_results`.
   Você pode manter o `frames-watcher` **desligado** (o Agent fala direto com o webhook).

### Opção B — **API paga** mantendo o frames-watcher

1. Defina no `frames-watcher`:

   * `BACKEND=rekor_api`
   * `REKOR_API_URL=https://...` (fornecido pelo provedor)
   * `REKOR_API_KEY=...`
2. Ajuste, se necessário, o mapeamento de campos em `frames-watcher/backend.py::recognize_rekor_api` (para garantir que o retorno seja `alpr_results`).
3. Reinicie o serviço:

   ```bash
   docker compose restart frames-watcher
   ```
4. **O que muda:** o watcher continuará olhando a pasta `frames/`, mas agora usará **reconhecimento real** via API paga.
   Você pode usar **SINK=file** (NDJSON) ou **SINK=webhook** (mesmo webhook da opção A).

> **Escolha A ou B** — ambas convergem para o **mesmo webhook** ou para o **mesmo NDJSON**, então seus consumidores downstream não mudam.

---

## O que cada arquivo faz (rápido)

* `frames-watcher/watcher.py`
  Observa `./frames/` (polling + rescan), aplica *debounce*, dedupe com *locks*, processa cada arquivo **uma vez** e move para `./frames/processed/`.
  Chama `backend.build_payload(...)` e depois `backend.emit(...)`.

* `frames-watcher/backend.py`

  * `recognize_mock(...)`: gera placa determinística a partir do hash da imagem (demo realista).
  * `recognize_rekor_api(...)`: **placeholder** para usar a API oficial (preencher URL/chave/contrato).
  * `build_payload(...)`: retorna um `alpr_results` compatível com o Rekor Agent.
  * **Sinks**:

    * `sink_file(...)`: grava **NDJSON** simplificado (1 linha = 1 leitura).
    * `sink_webhook(...)`: envia o `alpr_results` ao webhook.
  * `emit(...)`: decide o destino com base em `SINK`.

* `webhook/app.py`
  Recebe `alpr_results` e devolve JSON **simplificado** `{ plate, state, ... }`. Em produção, é para onde o **Agent** vai postar.

---

## Troubleshooting (Windows/Docker Desktop)

* O watcher usa **PollingObserver** + **revarredura** para driblar a falta de eventos nativos em bind mounts.
  Se ainda perder arquivos:

  * aumente `DEBOUNCE_MS` (ex.: `800`);
  * reduza `RESCAN_SECONDS` (ex.: `2`);
  * verifique que os arquivos aparecem dentro do container:
    `docker compose exec frames-watcher sh -lc 'ls -lah /frames && ls -lah /frames/processed'`.
* Arquivos não devem ser reprocessados: eles são **movidos** para `processed/`.



