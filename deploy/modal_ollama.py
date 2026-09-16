"""Modal app: serves Ollama (embedding + generation models) as a public HTTPS endpoint.

Not application code -- deployment infrastructure for ERP-037's live test deployment. The exposed
endpoint speaks Ollama's native HTTP API directly (no custom proxy layer), so
`app.generation.client.OllamaLLMClient`/`app.embedding.client.OllamaEmbeddingClient` work against
it completely unchanged once `GENERATION_MODEL`/`EMBEDDING_MODEL`/the respective `*_OLLAMA_HOST`
settings point at this endpoint's URL -- see docs/deployment.md and the shared cross-project
infrastructure-options.md tracked outside this repo (see ADR-008).

Deploy:  uv run modal deploy deploy/modal_ollama.py
Models are pulled once at image-build time (baked into the image layer), not on every cold start.
"""

import os
import subprocess

import modal

GENERATION_MODEL = "gemma3:4b"
EMBEDDING_MODEL = "nomic-embed-text"

image = (
    modal.Image.debian_slim()
    .apt_install("curl", "zstd", "procps")
    .run_commands(
        # Ollama's own install script (not a hardcoded release-asset URL/format, which has
        # changed before -- e.g. .tgz -> .tar.zst) tracks whatever packaging they currently use.
        "curl -fsSL https://ollama.com/install.sh | sh",
    )
    .run_commands(
        "ollama serve > /tmp/ollama-build.log 2>&1 & "
        "sleep 5 && "
        f"ollama pull {EMBEDDING_MODEL} && "
        f"ollama pull {GENERATION_MODEL} && "
        "pkill ollama"
    )
)

app = modal.App(name="self-hosted-rag-platform-ollama", image=image)


@app.cls(gpu="T4", scaledown_window=300)
class OllamaServer:
    """One container: runs `ollama serve`, exposed directly as a public HTTPS endpoint."""

    @modal.web_server(port=11434, startup_timeout=60)
    def serve(self) -> None:
        """Start Ollama's own HTTP server; Modal proxies external traffic straight to port 11434.

        Ollama defaults to binding 127.0.0.1 only -- Modal's proxy connects from outside that
        namespace, so it must be told to bind 0.0.0.0 explicitly via OLLAMA_HOST.
        """
        env = {**os.environ, "OLLAMA_HOST": "0.0.0.0:11434"}
        subprocess.Popen(["ollama", "serve"], env=env)
