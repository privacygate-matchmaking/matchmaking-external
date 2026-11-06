from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Release(Strict):
    file_id: UUID
    member_id: UUID
    recipient_id: UUID
    consent_id: UUID
    purpose: Literal['introduction']
    expires_at: AwareDatetime


class Consent(Strict):
    counterpart_member_id: UUID
    item: Literal['profile','photo','contact']
    purpose: Literal['introduction']
    expires_at: AwareDatetime


class Empty(Strict):
    pass
