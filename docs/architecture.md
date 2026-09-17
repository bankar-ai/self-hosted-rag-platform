# Architecture

## Project Overview

This repository contains a production-grade Enterprise Retrieval-Augmented Generation (RAG) platform.

This is NOT a tutorial project.

Every design decision should resemble what would be implemented in a real enterprise software product.

The primary objective is to build a modular, scalable, testable and production-ready RAG platform that demonstrates software engineering excellence.

The platform will later become the foundation for additional repositories including:

- Agentic AI
- LLMOps & Evaluation Platform

Therefore, maintainability and extensibility are more important than rapid feature development.

**Design constraint for the future LLMOps & Evaluation Platform** (noted 2026-09-06, before that repo exists): it must be a **generalized, standalone platform**, not something built specifically for this RAG platform. Other projects — including this one and the future Agentic AI project — should be able to integrate with it as clients (via an API/SDK contract), rather than it being coupled to this codebase's internals. Concretely, this means:

- It lives in its own repository, not as a module inside `self-hosted-rag-platform`.
- It exposes a project-agnostic ingestion/tracing API and dataset/test-set management, so any LLM-based project can send it traces and evaluation data, not just RAG pipelines.
- This repo's own "Evaluation" project goal (`docs/roadmap.md`) should be scoped as a client integration against that future platform where practical, rather than a bespoke one-off evaluation harness built only for this repo — revisit this repo's Evaluation ticket's design once the platform's API contract exists.
- The platform itself is a multi-subsystem product (tracing/ingestion, dataset management, evaluation runners, experiment tracking, dashboards, possibly prompt versioning) and will need its own decomposition into phased sub-projects when work on it actually starts — don't scope it as a single ticket.

**Shared cross-project infrastructure reference** (noted 2026-09-08, see ADR-008): `D:\github-projects\infrastructure-options.md`, a sibling file outside this repo's git history, tracks researched hosting/compute/database/GPU/LLM-API options evaluated against a "live 2-3 months, then torn down" constraint shared by this repo and the future Agentic AI, PEFT/LoRA, and LLMOps repos. Each future repo should reference that shared file rather than re-deriving its own version. This repo's own deployment execution against it is tracked in `.ai/tickets/ERP-037.md`.

## Project Goals

The project must demonstrate:

- Enterprise software architecture
- Production-ready FastAPI backend
- Hybrid Retrieval
- PageIndex-inspired retrieval
- FAISS vector search
- BM25 retrieval
- Local LLM inference using Ollama
- Modular RAG pipeline
- Authentication
- Evaluation
- Observability
- Docker deployment
- CI/CD
- Comprehensive testing

## Project Philosophy

We are building software as if it will be deployed in production.

Every implementation should prioritize

- correctness
- maintainability
- extensibility
- readability
- engineering quality

over speed of implementation.

When multiple approaches are available, recommend the one that would be selected by an experienced backend AI engineer building an enterprise product.

## Architecture Principles

Follow feature-oriented architecture.

Separate responsibilities into independent modules.

Business logic must never exist inside API routes.

API routes should only:

- validate request
- call service layer
- return response

Use dependency injection whenever appropriate.

Prefer composition over inheritance.

All dependencies must be open-source and free to use. Paid or proprietary APIs/services (hosted LLMs, managed vector DBs, paid embedding APIs, etc.) are out of scope unless explicitly approved as an exception.

**Keep the always-on host thin; offload anything CPU/memory-heavy to serverless compute.** The
live deployment's always-on host (currently a GCP `e2-micro` VM, ~958MB RAM, chosen for its
permanent free tier) exists to serve requests, not to run heavy compute. Established twice now:
LLM/embedding inference runs on Modal (GPU, ERP-037), not on the VM; `docling`'s document-parsing
fallback runs on Cloud Run (CPU/memory, decided 2026-09-17 after it took the whole VM down mid-
demo — see `.ai/sessions/` around that date), not in-process on the VM. Before adding anything
new that's meaningfully CPU- or memory-heavy (a new ML model, a batch job, anything beyond simple
request/response logic), default to a separate serverless service the VM calls out to, not code
that runs in the VM's own process. Don't wait for it to crash the VM first to make this call.

## Technology Stack

Operating System

- Windows (primary), Linux compatibility maintained whenever possible

Language

- Python 3.12

Package Manager

- uv

Backend

- FastAPI

Validation

- Pydantic v2

LLM

- Ollama, primary development model Qwen3

Embeddings

- Nomic Embed

Vector Database

- FAISS

Testing

- Pytest

Containerization

- Docker

Version Control

- Git

IDE

- Claude Code

## Architecture Diagram

The full target-state architecture — API layer, service layer, data layer, core/infra, cross-cutting concerns, and deployment — is diagrammed in `docs/diagrams/architecture.drawio` (source, editable in [draw.io](https://app.diagrams.net/)) and `docs/diagrams/architecture.png` (exported image). Components not yet built are shown dashed/grey; components that exist today are shown solid/green. See `docs/superpowers/specs/2026-07-26-system-architecture-design.md` for the design rationale.
