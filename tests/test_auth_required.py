"""Regression test for the feature's headline guarantee: no more anonymous access.

Finding 5 (final whole-branch review): no test anywhere asserted 401-without-a-token on any
protected endpoint, so removing `Depends(get_current_user)` from a router would not fail any
existing test. This exercises all six protected routes with no `Authorization` header at all
-- FastAPI's `OAuth2PasswordBearer` (auto_error=True) rejects with 401 before any business
logic (or even request-body validation) runs, so the request bodies below are minimal/dummy.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_DUMMY_JOB_ID = "does-not-exist"
_DUMMY_CONVERSATION_ID = str(uuid.uuid4())


def _call_ingestion_pdf():
    return client.post(
        "/ingestion/pdf",
        files={"file": ("dummy.pdf", io.BytesIO(b"%PDF-dummy"), "application/pdf")},
    )


def _call_ingestion_job_status():
    return client.get(f"/ingestion/jobs/{_DUMMY_JOB_ID}")


def _call_retrieval_query():
    return client.post("/retrieval/query", json={"query": "anything"})


def _call_generation_query():
    return client.post("/generation/query", json={"query": "anything"})


def _call_generation_query_stream():
    return client.post("/generation/query/stream", json={"query": "anything"})


def _call_generation_warmup():
    return client.post("/generation/warmup")


def _call_get_conversation():
    return client.get(f"/conversations/{_DUMMY_CONVERSATION_ID}")


def _call_admin_list_users():
    return client.get("/admin/users")


def _call_admin_update_user():
    return client.patch(f"/admin/users/{_DUMMY_CONVERSATION_ID}", json={"is_active": False})


def _call_admin_revoke_sessions():
    return client.post(f"/admin/users/{_DUMMY_CONVERSATION_ID}/revoke-sessions")


def _call_admin_delete_user():
    return client.delete(f"/admin/users/{_DUMMY_CONVERSATION_ID}")


_PROTECTED_ROUTES = [
    pytest.param(_call_ingestion_pdf, id="POST /ingestion/pdf"),
    pytest.param(_call_ingestion_job_status, id="GET /ingestion/jobs/{job_id}"),
    pytest.param(_call_retrieval_query, id="POST /retrieval/query"),
    pytest.param(_call_generation_query, id="POST /generation/query"),
    pytest.param(_call_generation_query_stream, id="POST /generation/query/stream"),
    pytest.param(_call_generation_warmup, id="POST /generation/warmup"),
    pytest.param(_call_get_conversation, id="GET /conversations/{id}"),
    pytest.param(_call_admin_list_users, id="GET /admin/users"),
    pytest.param(_call_admin_update_user, id="PATCH /admin/users/{user_id}"),
    pytest.param(_call_admin_revoke_sessions, id="POST /admin/users/{user_id}/revoke-sessions"),
    pytest.param(_call_admin_delete_user, id="DELETE /admin/users/{user_id}"),
]


@pytest.mark.parametrize("make_request", _PROTECTED_ROUTES)
def test_protected_route_returns_401_without_authorization_header(make_request):
    response = make_request()
    assert response.status_code == 401


def test_health_is_deliberately_public_unlike_every_route_above():
    # ERP-090/ERP-091: an external uptime check and the pre-auth login page both need to call
    # this with no token -- the contrast with every route above is the point of this test.
    response = client.get("/health")
    assert response.status_code == 200
