"""Request bodies. Responses are returned as the plain dicts queries.py hands
back -- psycopg's dict_row already shapes them the way the SQL names columns,
and FastAPI's jsonable_encoder handles Decimal and date on its own, so a
second, parallel set of response schemas would just be upkeep without a
payoff at this size.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

# A barcode: EAN-8 to GTIN-14, digits only. A rule that watches "123" never
# matches anything, so it is refused rather than stored.
Ean = Annotated[str, Field(pattern=r"^\d{8,14}$")]


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class InviteMemberRequest(BaseModel):
    # Only the shape: Cognito, which sends the invitation there, has the
    # last word.
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MerchantRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    province: str
    locality: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SubscribeBranchRequest(BaseModel):
    company_id: int
    banner_id: int
    branch_id: str
    label: str | None = None
    radius_km: float = Field(default=5.0, gt=0, le=10.0)


class UpdateBranchRequest(BaseModel):
    label: str | None = None
    radius_km: float = Field(gt=0, le=10.0)


class CreateRuleRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    company_id: int
    banner_id: int
    branch_id: str
    radius_km: float | None = Field(default=None, gt=0, le=10.0)
    target_company_id: int | None = None
    target_banner_id: int | None = None
    target_branch_id: str | None = None
    threshold_pct: float = Field(default=5.0, ge=0)
    products: list[Ean] = Field(default_factory=list)


class UpdateRuleRequest(BaseModel):
    enabled: bool


class ReplaceRuleProductsRequest(BaseModel):
    products: list[Ean]


class CreateUploadRequest(BaseModel):
    feed_date: str
    filename: str = Field(min_length=1, max_length=200)
