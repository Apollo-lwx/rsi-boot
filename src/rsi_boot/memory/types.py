"""MemoryDoc schema, legacy type map, and directory-derived status."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

MEMORY_TYPES = (
    "prohibition",
    "convention",
    "documentation",
    "skill",
    "episode",
    "gene_case",
    "teaching_case",
    "pattern",
)
LEGACY_TYPE_MAP = {
    "experience": "convention",
    "architecture": "documentation",
    "faq": "documentation",
}


class MemoryDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    type: str
    title: str = Field(max_length=120)
    content: str = Field(max_length=20000)
    status: str = "active"
    domain: str | None = None
    tags: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    source: str | None = None
    description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    refs: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)
    path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _body_alias_to_content(cls, data):
        if isinstance(data, dict) and "content" not in data and "body" in data:
            data = dict(data)
            data["content"] = data.pop("body")
        return data


def type_from_legacy(content_type: str) -> tuple[str, dict]:
    """返回 (type, extra_update)。未列出的 type 原样；architecture/faq/experience 按表。"""
    mapped = LEGACY_TYPE_MAP.get(content_type)
    if mapped is None:
        return content_type, {}
    return mapped, {"legacy_type": content_type}


def status_from_path(rel_posix: str) -> str:
    """memory/pending/** → pending_review；memory/archive/** → archived；
    teaching-cases/pending/ → active；其余 memory/ → active。"""
    parts = rel_posix.replace("\\", "/").strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "memory":
        if parts[1] == "pending":
            return "pending_review"
        if parts[1] == "archive":
            return "archived"
    return "active"
