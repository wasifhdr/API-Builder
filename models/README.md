# Models & llama-server binary (not checked into git)

This project needs two things that don't belong in version control: the `llama-server` binary
and a `.gguf` model file. Both are gitignored (`llama/`, `models/*.gguf`).

## 1. llama-server binary (CUDA build)

Go to the **llama.cpp releases page** on GitHub (`ggml-org/llama.cpp`, "Releases" tab) and
download the prebuilt **Windows CUDA** zip matching your installed CUDA toolkit version (e.g.
`llama-<version>-bin-win-cuda-cu12.x-x64.zip` — check `nvidia-smi` for your CUDA version).
Extract it into `llama/` at the repo root, so `llama/llama-server.exe` exists.

Flag spellings (`-ngl`, `--cache-type-k`, etc.) can drift between releases — run
`llama\llama-server.exe --help` and cross-check against `scripts/run-llama.ps1` if the script
fails to start.

## 2. GGUF model

Default target: **Qwen2.5-Coder-7B-Instruct**, quantized to **Q4_K_M**, from a GGUF quantizer
repo on Hugging Face (search `Qwen2.5-Coder-7B-Instruct GGUF`, e.g. bartowski's quantizations).
Download the `Q4_K_M.gguf` file and place it at `models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf`
(or update the path in `scripts/run-llama.ps1`).

Expected VRAM at `-c 4096` with q8_0 KV cache: ~5.1 GB (see docs/BLUEPRINT.md §6.1). Check
`nvidia-smi` first — if your integrated GPU isn't driving the display, the dedicated GPU may have
less headroom than expected.

**If it OOMs on a 6GB card:**
- Try `-ngl 28` (partial offload) instead of `-ngl 99` (full offload) in `run-llama.ps1`, or
- Switch to `Qwen2.5-Coder-3B-Instruct`, quantized `Q5_K_M` (~2.4 GB) — same search approach on
  Hugging Face, update both the filename in `run-llama.ps1` and here.

The app must keep working with the LLM off — set `LLM_ENABLED=false` in `.env` and OpenAPI specs
fall back to deterministic template prose (docs/BLUEPRINT.md §6.4).
