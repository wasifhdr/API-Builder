# Runs llama-server natively (not in Docker) for direct CUDA access.
# Requires llama-server.exe under llama/ and a .gguf model under models/ — see models/README.md.
# VRAM budget assumes an RTX 4050 6GB laptop; see docs/BLUEPRINT.md §6.1 if you need to
# tune flags for a different model/GPU.

& "$PSScriptRoot\..\llama\llama-server.exe" `
  -m "$PSScriptRoot\..\models\Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" `
  --host 127.0.0.1 --port 8080 `
  -ngl 99 -c 4096 --flash-attn `
  --cache-type-k q8_0 --cache-type-v q8_0 `
  --parallel 1 --no-webui
