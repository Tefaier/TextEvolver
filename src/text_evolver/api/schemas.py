"""Public JSON contracts; internal storage keys and paths never appear here."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(max_length=64)]
Matcher = Annotated[str, Field(min_length=1, max_length=128)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FandomRule(Schema):
    name: Text = "Pokemons"
    active: bool = False
    separation: int = Field(default=1, ge=1)
    support_value_1: bool = False
    support_value_2: bool = False


class UnitRule(Schema):
    phrase_from: Matcher
    phrase_to: Text
    conversion: float = Field(allow_inf_nan=False)
    can_be_word: bool = False


class PhraseRule(Schema):
    phrase_from: Matcher
    phrase_to: Text
    direct: bool = False
    mutations: bool = False
    regex: bool = False


class ImageRule(Schema):
    phrase: Matcher
    separation: int = Field(default=1, ge=1)
    explanation: Text = ""
    mutations: bool = False


class SettingCreate(Schema):
    name: Annotated[str, Field(min_length=1, max_length=64)] = "New_setting"
    public: bool = False
    clean_empty: bool = False
    convert_to_utf: bool = False
    use_comma_separator: bool = False
    expect_feet: bool = False
    fandoms: list[FandomRule] = Field(default_factory=lambda: [FandomRule()])
    units: list[UnitRule] = Field(default_factory=list)
    phrases: list[PhraseRule] = Field(default_factory=list)


class SettingPatch(Schema):
    name: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    public: bool | None = None
    clean_empty: bool | None = None
    convert_to_utf: bool | None = None
    use_comma_separator: bool | None = None
    expect_feet: bool | None = None
    fandoms: list[FandomRule] | None = None
    units: list[UnitRule] | None = None
    phrases: list[PhraseRule] | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Setting fields cannot be null")
        return self


class ImageFileView(Schema):
    id: int
    size_bytes: int
    position: int


class ImageView(ImageRule):
    id: int
    files: list[ImageFileView]


class SettingView(SettingCreate):
    id: int
    owner_id: UUID
    owner_name: str
    deletion_requested: bool
    images: list[ImageView]


class TaskView(Schema):
    id: int
    setting_id: int | None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    cancellation_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total_files: int
    completed_files: int
    can_download: bool


class Page[T](Schema):
    items: list[T]
    total: int
    page: int
    page_size: int


class DeletionView(Schema):
    status: Literal["pending_deletion"] = "pending_deletion"
