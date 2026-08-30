"""Request and response DTOs for the web API.

Deliberately **not** `automa.contracts` - that module's own docstring calls itself
"the entire surface where a model is allowed to influence the game", kept
deliberately narrow, and a `MoveRequest` would dilute a claim the README makes. The
one legitimate reuse is `contracts.EffectOp` for the catalog editor's effect payload.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from automa.contracts import EffectOp


class HealthResponse(BaseModel):
    ok: bool = True


class ConfigResponse(BaseModel):
    """Credential *presence* only - never a value. See `/api/config`'s docstring."""

    models: dict[str, str]
    trajectory_dir: str
    credentials: dict[str, bool]


class GameSummary(BaseModel):
    #: The module name - what `load_game` resolves, and the only addressable identity.
    name: str
    #: The module's own `NAME`, which is where its catalog lives. Two installed
    #: modules may legitimately declare the same one.
    declared_name: str | None = None
    players: list[str] | None
    conforms: bool
    problems: list[str] = Field(default_factory=list)
    catalog: dict[str, int]


class GameDetail(GameSummary):
    supported_ops: list[str] | None = None
    terms_declared: bool = False
    state_keys: list[str] = Field(default_factory=list)


class ReloadResponse(BaseModel):
    name: str
    players: list[str]
    conforms: bool


class VocabularyResponse(BaseModel):
    operations: dict[str, dict[str, Any]]
    named_fields: list[str]


class CardEntryOut(BaseModel):
    card_type: str
    effects: list[dict[str, Any]]
    description: str
    unresolved: str
    learned_at: str
    playable: bool


class CatalogResponse(BaseModel):
    game: str
    known: list[CardEntryOut]
    parked: list[CardEntryOut]
    audit: list[str]
    terms_declared: bool
    terms_hint: str


class LearnCardRequest(BaseModel):
    effects: list[EffectOp]
    description: str = ""


class LearnCardResponse(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


class ParkCardRequest(BaseModel):
    description: str = ""
    reason: str


class DescribeCardRequest(BaseModel):
    description: str = Field(min_length=1)


class DescribeCardResponse(BaseModel):
    ok: bool
    effects: list[dict[str, Any]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    unresolved: str = ""
    llm_calls: int = 0
    cost_usd: float = 0.0


class UploadResponse(BaseModel):
    job_id: str
    filename: str
    kind: str
    detail: str


class CompileRequest(BaseModel):
    game: str
    material_id: str
    cards_id: str | None = None
    use_catalog: bool = False
    attempts: int = Field(default=4, ge=1, le=10)
    matches: int = Field(default=200, ge=1, le=2000)


class CompileApproveRequest(BaseModel):
    acknowledged: Literal[True]
    overwrite: bool = False


class SessionCreateRequest(BaseModel):
    game: str
    mode: Literal["live", "offline"] = "offline"
    simulate_deal: bool = False
    use_catalog: bool = True
    seed: int | None = None
    delay: float = Field(default=0.0, ge=0.0, le=30.0)
    max_plies: int = Field(default=1000, ge=1, le=100_000)


class MoveRequest(BaseModel):
    index: int


class AnswerRequest(BaseModel):
    text: str | None = None
