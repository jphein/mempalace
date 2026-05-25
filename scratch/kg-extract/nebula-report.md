# Nebula deploy report — llama-server for KG triple extraction

**Status:** complete, service running and verified
**Host:** familiar.jphe.in (10.0.6.124)
**Date:** 2026-05-25

## Model

- **Source:** `https://huggingface.co/unsloth/Phi-4-mini-instruct-GGUF`
- **File:** `Phi-4-mini-instruct-Q4_K_M.gguf`
- **Local path:** `/var/cache/llama/models/Phi-4-mini-instruct-Q4_K_M.gguf`
- **Size:** 2,491,874,272 bytes (~2.32 GiB)
- **Downloaded via:** `sudo wget` directly on familiar

## Endpoint

- **URL:** `http://familiar.jphe.in:11436/v1/chat/completions`
- **Health:** `http://familiar.jphe.in:11436/health` → `{"status":"ok"}`
- **Models list:** `http://familiar.jphe.in:11436/v1/models`
- **Model alias (API `model` field):** `phi-4-mini`
- **Schema:** OpenAI-compatible (chat completions)
- **Listens:** `0.0.0.0:11436` (reachable from katana over LAN)
- **Reachable from katana:** verified — see sample below

## Systemd unit

`/etc/systemd/system/llama-server-extractor.service` — installed, enabled, active.

```ini
[Unit]
Description=llama.cpp inference server for KG triple extraction
After=network.target

[Service]
Type=simple
User=jp
Environment=CUDA_VISIBLE_DEVICES=0
ExecStart=/opt/llama.cpp/build/bin/llama-server \
  --model /var/cache/llama/models/Phi-4-mini-instruct-Q4_K_M.gguf \
  --port 11436 --host 0.0.0.0 \
  --n-gpu-layers 999 \
  --ctx-size 4096 \
  --parallel 8 \
  --threads 12 \
  --cont-batching \
  --alias phi-4-mini
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`CUDA_VISIBLE_DEVICES=0` added to pin the model to the P102 (10GB card)
so it cannot accidentally land on the GTX 970 (4GB) where the embedding
server already lives.

## Sample request + response

**Briefing test (single triple, JSON object):**

```bash
curl -s http://familiar.jphe.in:11436/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "phi-4-mini",
    "messages": [
      {"role": "user", "content": "Extract a triple from: JP works on memorypalace. Reply with JSON."}
    ],
    "max_tokens": 80
  }'
```

Response (excerpt):

```json
{
  "choices": [{
    "finish_reason": "stop",
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "```json\n{\n  \"subject\": \"JP\",\n  \"predicate\": \"works on\",\n  \"object\": \"memorypalace\"\n}\n```"
    }
  }],
  "model": "phi-4-mini",
  "usage": {"completion_tokens": 31, "prompt_tokens": 19, "total_tokens": 50},
  "timings": {
    "prompt_per_second": 364.87,
    "predicted_per_second": 70.82
  }
}
```

**Multi-triple JSON list (more typical of Morpheus's extraction workload):**

Prompt: `"Extract triples from the following sentence as JSON list of [subject,predicate,object] arrays. Sentence: JP works on memorypalace and lives in California."`

Response content:

```json
[
    ["JP", "works on", "memorypalace"],
    ["JP", "lives in", "California"]
]
```

Timing: `prompt_per_second ≈ 1004`, `predicted_per_second ≈ 74`.

## VRAM usage observed

During active inference (P102 = CUDA0, GTX 970 = CUDA1):

```
index, name,                    memory.used, memory.free, utilization.gpu
0,     NVIDIA P102-100,         3437 MiB,    6708 MiB,    0 %
1,     NVIDIA GeForce GTX 970,  323 MiB,     3706 MiB,    0 %
```

- **P102 used: 3,437 MiB / 10,143 MiB total**
- **P102 free: 6,708 MiB (well above the 4 GiB headroom requirement)**
- GTX 970 untouched by this service; existing embedding server still
  holds its 323 MiB on GPU1 as before.
- Pre-deploy state was `P102 used: 0 MiB, GTX 970 used: 323 MiB` — the
  embedding server's footprint did not change.

## Verification checklist

- [x] Model downloaded and verified (2.49 GiB on familiar)
- [x] Systemd unit installed at `/etc/systemd/system/llama-server-extractor.service`
- [x] `systemctl daemon-reload && systemctl enable --now` succeeded
- [x] Service `active (running)` since 2026-05-25 13:13:05 PDT
- [x] `/health` returns 200 (`{"status":"ok"}`) from both familiar and katana
- [x] Briefing curl returns valid JSON triple
- [x] Model loaded on **P102 / GPU0** (not GTX 970)
- [x] P102 free VRAM > 4 GiB during inference (6.7 GiB observed)
- [x] Embedding server on `:11435` (PID 2956) untouched — `systemctl is-active`
      still reports `active`, same PID as before
- [x] No Ollama installation performed by this task

## Notes for downstream agents

- Use `model: "phi-4-mini"` in the OpenAI chat completions payload.
- llama.cpp wraps JSON in markdown fences (` ```json ... ``` `). The
  extractor should strip fences before parsing — Morpheus is already
  aware (worker design uses regex extraction of the first balanced
  JSON object / array).
- `--ctx-size 4096` and `--parallel 8` mean up to 8 concurrent requests,
  each capped at ~4k context. For long drawer content, chunk on the
  caller side before sending.
- `--cont-batching` is enabled, so concurrent requests interleave
  efficiently. The async worker pool design in Morpheus's module can
  run 4–8 in flight.
- Pre-existing port 11435 is **llama-server**, not Ollama, despite the
  systemd unit name `ollama-embed.service` — its description and
  filename are historical artifacts from when an Ollama binary lived
  there. The relevant fact: it serves embeddings and we left it alone.
