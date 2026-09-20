"""Modal app: a CI-only Ollama endpoint, isolated from the production endpoint (ERP-083).

Not application code -- deployment infrastructure for the scheduled evaluation-gate CI
workflow (`.github/workflows/evaluation-gate.yml`). This is a deliberate clone of
`deploy/modal_ollama.py`, not a shared/parameterized script: the production endpoint already
had a real incident (2026-09-17) where it was disabled after crossing Modal's
$1-usable-without-a-payment-method threshold -- a second, separate Modal app keeps CI's own
usage/quota/failure blast radius from ever touching production again, and keeps the two
independently deployable/tear-down-able (see the cross-project gcp-deployment-tracker.md).

Deploy:  uv run modal deploy deploy/modal_ollama_ci.py
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

app = modal.App(name="self-hosted-rag-platform-ollama-ci", image=image)


@app.cls(gpu="T4", scaledown_window=300)
class OllamaServer:
    """One container: runs `ollama serve`, exposed directly as a public HTTPS endpoint."""

    @modal.web_server(port=11434, startup_timeout=60)
    def serve(self) -> None:
        """Start Ollama's own HTTP server; Modal proxies external traffic straight to port 11434."""
        env = {**os.environ, "OLLAMA_HOST": "0.0.0.0:11434"}
        subprocess.Popen(["ollama", "serve"], env=env)
