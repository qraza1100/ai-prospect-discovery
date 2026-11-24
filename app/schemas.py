from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TargetType = Literal["individual", "business"]
EntityKind = Literal["individual", "business", "unknown"]
ProspectEntity = Literal["individual", "business", "organization"]


class Platform(StrEnum):
    GOOGLE = "Google"
    LINKEDIN = "LinkedIn"
    X = "X"
    YOUTUBE = "YouTube"
    INSTAGRAM = "Instagram"
    WHATSAPP = "WhatsApp"
    FACEBOOK = "Facebook"
    EMAIL = "Email"
    THREADS = "Threads"
    PHONE = "Phone"


PLATFORM_ORDER: dict[Platform, int] = {
    p: i
    for i, p in enumerate(
        [
            Platform.GOOGLE,
            Platform.LINKEDIN,
            Platform.FACEBOOK,
            Platform.INSTAGRAM,
            Platform.X,
            Platform.YOUTUBE,
            Platform.THREADS,
            Platform.EMAIL,
            Platform.WHATSAPP,
            Platform.PHONE,
        ]
    )
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class ProspectDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(
        min_length=3,
        max_length=300,
        description="Natural-language description of the prospects to find.",
        examples=["Football lovers in Washington DC in USA"],
    )
    max_results: int | None = Field(
        default=None, ge=1, le=100, description="Upper bound on returned prospects."
    )

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        cleaned = " ".join(_CONTROL_CHARS.sub(" ", unicodedata.normalize("NFKC", value)).split())
        if sum(ch.isalpha() for ch in cleaned) < 3:
            raise ValueError("query must contain at least three letters")
        return cleaned


class Location(BaseModel):
    city: str | None = None
    state: str | None = None
    country: str | None = None
    country_code: str | None = Field(default=None, description="ISO 3166-1 alpha-2, lowercase.")

    def is_empty(self) -> bool:
        return not (self.city or self.state or self.country)

    def display(self, level: Literal["city", "state", "country"] = "city") -> str | None:
        parts: list[str | None]
        if level == "city":
            parts = [self.city or self.state, self.country]
        elif level == "state":
            parts = [self.state, self.country]
        else:
            parts = [self.country]
        text = ", ".join(p for p in parts if p)
        return text or None


class SearchIntent(BaseModel):
    interest: str
    audience: str
    location: Location = Field(default_factory=Location)
    target_type: TargetType = "individual"
    keywords: list[str] = Field(default_factory=list)
    role_terms: list[str] = Field(default_factory=list)
    location_aliases: list[str] = Field(default_factory=list)
    source: Literal["llm", "rules"] = "rules"


class SocialLink(BaseModel):
    platform: Platform
    link: str


class Relevance(BaseModel):
    score: float = Field(ge=0, le=1)
    interest_match: bool
    location_match: bool
    method: Literal["llm", "rules"]
    evidence: list[str] = Field(default_factory=list)


class Prospect(BaseModel):
    name: str = Field(min_length=1)
    location: str | None = None
    entity_type: ProspectEntity = "individual"
    social_link: list[SocialLink] = Field(min_length=1)
    relevance: Relevance


class PlatformStatus(BaseModel):
    platform: Platform
    status: Literal["ok", "failed", "skipped"]
    profiles_found: int = 0
    detail: str | None = None


class ProspectDiscoveryResponse(BaseModel):
    success: Literal[True] = True
    query: str
    total_prospects: int
    prospects: list[Prospect]
    intent: SearchIntent
    platforms: list[PlatformStatus]

    @model_validator(mode="after")
    def _check_total(self) -> ProspectDiscoveryResponse:
        if self.total_prospects != len(self.prospects):
            raise ValueError("total_prospects must equal the number of prospects")
        return self


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
    query: str | None = None
    platforms: list[PlatformStatus] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    search_provider: str | None
    llm_provider: str | None
    youtube_api: bool


@dataclass(slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    query: str
    source: Platform
    position: int = 0


@dataclass(slots=True)
class ContactSet:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    whatsapp: set[str] = field(default_factory=set)

    def update(self, other: ContactSet) -> None:
        self.emails |= other.emails
        self.phones |= other.phones
        self.whatsapp |= other.whatsapp

    def is_empty(self) -> bool:
        return not (self.emails or self.phones or self.whatsapp)

    def identity_keys(self) -> set[str]:
        return (
            {f"email:{e}" for e in self.emails}
            | {f"phone:{p}" for p in self.phones}
            | {f"phone:{w}" for w in self.whatsapp}
        )


@dataclass(slots=True)
class ProfileCandidate:
    id: str
    platform: Platform
    url: str
    handle: str | None
    name: str | None
    kind: EntityKind
    title: str = ""
    snippet: str = ""
    source_url: str = ""
    sources: set[str] = field(default_factory=set)
    contacts: ContactSet = field(default_factory=ContactSet)
    linked_urls: set[str] = field(default_factory=set)
    page_text: str = ""
    page_location: str = ""

    @property
    def text(self) -> str:
        parts = [self.title, self.snippet, self.page_text, self.page_location]
        return " ".join(p for p in parts if p)
