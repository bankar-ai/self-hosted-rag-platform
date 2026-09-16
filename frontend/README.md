# Frontend

Vite + React + TypeScript SPA for the Self-Hosted RAG Platform. See
`docs/superpowers/specs/2026-09-16-web-ui-design.md` for the design and
`docs/superpowers/plans/2026-09-16-web-ui.md` for how it was built.

## Local development

```
cp .env.example .env.local   # set VITE_API_BASE_URL to your local backend, e.g. http://localhost:8000
npm install
npm run dev
```

Run tests with `npm test`, type-check with `npx tsc -b`.

## Deployment (Vercel)

Import this repo into Vercel, set the project root to `frontend/`, and set the
`VITE_API_BASE_URL` environment variable to the live backend's URL
(`https://34-31-5-88.sslip.io` as of this writing). Vercel auto-detects the Vite framework
preset — no `vercel.json` needed.

The backend must also have this frontend's deployed Vercel origin in its
`CORS_ALLOWED_ORIGINS` env var (see `app/core/cors.py`), or every request will be blocked by
the browser regardless of what the API itself would allow.
