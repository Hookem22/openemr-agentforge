from fastapi import FastAPI, Header, HTTPException
from langfuse import get_client
from pydantic import BaseModel, field_validator

from .config import settings
from .graph import run_turn

app = FastAPI(title="Clinical Co-Pilot Agent")


class ChatRequest(BaseModel):
    patient_id: str
    message: str
    conversation_history: list[dict] = []

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        # System-boundary validation: a blank message passed straight through would hit the
        # Anthropic API's "messages must have non-empty content" rule and raise mid-turn (a real
        # crash caught by eval/test_boundary_conditions.py). Reject cleanly here instead.
        if not v.strip():
            raise ValueError("message must not be empty")
        return v


class ChatResponse(BaseModel):
    verified_claims: list[dict]
    stripped_claims: list[dict]
    tool_failures: list[dict]
    strip_rate: float
    conversation_history: list[dict]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    # TEMPORARY: until the auth-bridge endpoint exists (agent-implementation.md decision #1), the
    # bearer token comes from either an explicit Authorization header (preferred, so this already
    # works the same way once the bridge exists) or the dev-only fallback token from settings.
    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization.split(" ", 1)[1]
    bearer_token = bearer_token or settings.dev_bearer_token
    if not bearer_token:
        raise HTTPException(status_code=401, detail="No bearer token provided (Authorization header or DEV_BEARER_TOKEN)")

    result = run_turn(
        patient_id=req.patient_id,
        bearer_token=bearer_token,
        user_message=req.message,
        prior_messages=req.conversation_history,
    )
    # Flush now rather than waiting for the SDK's background batch interval -- this is a
    # request/response call, not a long-running worker, so we want the trace visible immediately
    # (and don't want it lost if the dev server reloads between requests).
    get_client().flush()

    total = len(result["verified_claims"]) + len(result["stripped_claims"])
    strip_rate = (len(result["stripped_claims"]) / total) if total else 0.0

    return ChatResponse(
        verified_claims=result["verified_claims"],
        stripped_claims=result["stripped_claims"],
        tool_failures=result["tool_failures"],
        strip_rate=strip_rate,
        conversation_history=result["messages"],
    )
