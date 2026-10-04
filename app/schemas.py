"""Request and response contracts for /api/chat (build-plan §2)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecentItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entry_id: str = Field(max_length=80)
    kind: str = Field(max_length=20)
    layer: str = Field(default="summary", max_length=10)


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prev_entry_id: str | None = Field(default=None, max_length=80)
    recent: list[RecentItem] = Field(default_factory=list, max_length=10)
    repeat_count: int = Field(default=0, ge=0, le=50)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Hard cap only. The 800-character limit is checked after distress detection,
    # so a long message that signals distress still gets the distress response.
    message: str = Field(max_length=4000)
    sid: str = Field(default="", max_length=64)
    turn: int = Field(default=1, ge=1, le=1000)
    mode: Literal["", "offline"] = ""
    context: ChatContext = Field(default_factory=ChatContext)


Kind = Literal["answer", "abstain", "refer", "distress", "limit", "non_arabic", "smalltalk"]
Layer = Literal["summary", "explain", "body"]


class ChatResponse(BaseModel):
    kind: Kind
    entry_id: str | None = None
    layer: Layer | None = None
    degraded: bool = False
    version: str
    blocks: list[dict]
