# Cloud Run Docling Service

Standalone Cloud Run service running `docling`'s `DocumentConverter`, isolated from the main
app's VM. See `docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md` (ERP-047).

## Local testing

```
pip install -r requirements.txt
pytest test_main.py
```

Not run in the main project's CI/`uv run pytest` -- `docling` is deliberately not a root-project
dependency any more (that's the whole point of this service existing).

## Deploy

```
gcloud run deploy self-hosted-rag-platform-docling \
  --source deploy/cloud_run_docling \
  --region us-central1 \
  --no-allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 600 \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 3 \
  --project self-hosted-rag-platform
```

`--concurrency 1` matters: `docling` is CPU/memory-heavy per request, and letting Cloud Run route
multiple concurrent requests into one container would let them compete for the same resources
that are already tightly budgeted. `--max-instances 3` bounds worst-case cost.

Then grant the VM's service account permission to call it (see `docs/deployment.md`'s docling
section for the exact command and how to find the VM's service account email).
