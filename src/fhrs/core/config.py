"""Settings + country profile (PROMPT.md, "The one switch" and N9).

The root .env selects a country; this module loads and validates the matching
profile at startup and fails fast on anything missing or malformed. No other layer
reads environment variables.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
COUNTRIES_DIR = REPO_ROOT / "countries"
REGISTRY_PATH = REPO_ROOT / "registry" / "checklists.yaml"


class Branding(BaseModel):
    site_name: str
    tagline: str
    mark: str


class DataFiles(BaseModel):
    aliases: str
    institutions: str
    gazetteer: str
    boundary: str


class Footprint(BaseModel):
    tiers: list[Literal["A", "B", "C"]]


class Sources(BaseModel):
    insdc: list[str]


class CountryProfile(BaseModel):
    """Validated countries/<code>.yaml. `data_dir` is injected at load."""

    code: str
    name: str
    name_local: str = ""
    languages: list[str] = Field(min_length=1)
    branding: Branding
    data: DataFiles
    footprint: Footprint
    sources: Sources
    data_dir: Path

    @field_validator("code")
    @classmethod
    def _iso_alpha2(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError(f"country code must be ISO 3166-1 alpha-2 uppercase, got {v!r}")
        return v

    def data_path(self, key: Literal["aliases", "institutions", "gazetteer", "boundary"]) -> Path:
        p = self.data_dir / getattr(self.data, key)
        if not p.is_file():
            raise FileNotFoundError(f"country data file missing: {p}")
        return p


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FHRS_",
        env_file=(REPO_ROOT / ".env", REPO_ROOT / ".env.local"),
        extra="ignore",
    )

    country_code: str
    database_url: str
    payload_dir: Path = REPO_ROOT / "data" / "payloads"
    numbers_out: Path = REPO_ROOT / "build" / "numbers.json"
    disk_watermark_pct: int = Field(default=90, ge=1, le=99)

    @field_validator("country_code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def load_profile(self) -> CountryProfile:
        path = COUNTRIES_DIR / f"{self.country_code.lower()}.yaml"
        if not path.is_file():
            available = sorted(p.stem.upper() for p in COUNTRIES_DIR.glob("*.yaml"))
            raise FileNotFoundError(
                f"no country profile for {self.country_code!r} ({path}); "
                f"available: {available}; add one from countries/template.yaml"
            )
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        profile = CountryProfile(**doc, data_dir=COUNTRIES_DIR / self.country_code.lower())
        if profile.code != self.country_code:
            raise ValueError(
                f"profile file {path.name} declares code={profile.code!r}, "
                f"but FHRS_COUNTRY_CODE={self.country_code!r}"
            )
        # Fail fast: every declared data file must exist before we serve traffic.
        for key in ("aliases", "institutions", "gazetteer", "boundary"):
            profile.data_path(key)
        return profile


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_profile() -> CountryProfile:
    return get_settings().load_profile()
