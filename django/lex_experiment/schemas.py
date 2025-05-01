from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class Address(BaseModel):
    street: str = Field(description="Street number and name")
    city: str = Field(description="City name")
    province: str = Field(description="Province code")
    postal_code: str = Field(description="Postal code")
    country: str = Field(description="Country name")


class Representative(BaseModel):
    name: str = Field(description="Representative's name")
    address: Address = Field(description="Representative's address")


class Appellant(BaseModel):
    name: str = Field(description="Appellant's name")
    address: Address = Field(description="Appellant's address")


class TaxAppeal(BaseModel):
    court_number: str = Field(description="Tax Court of Canada Court No.")
    appellant: Appellant = Field(description="Appellant information")
    class_level: str = Field(description="Tax Court of Canada Class Level")
    filing_date: date = Field(description="Filed date of the Notice of Appeal")
    representative: Optional[Representative] = Field(
        description="Representative information"
    )
    taxation_years: List[str] = Field(description="List of taxation years")
    total_tax_amount: str = Field(description="Total tax amount")
    sections_referred: List[str] = Field(
        description="Sections or subsections referred to in the Notice of Appeal"
    )
