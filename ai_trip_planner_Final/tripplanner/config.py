"""Settings read from environment variables. Keys are never stored in code or files."""
import os
from dataclasses import dataclass
from typing import Mapping, Optional


def load_dotenv(path: str = ".env", environ=None) -> None:
    """Minimal .env reader (KEY=value lines). Real environment variables win, and
    empty values are ignored, so a half-filled .env.example copy does no harm."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and not environ.get(key):
            environ[key] = value


@dataclass(frozen=True)
class Settings:
    anthropic_key: Optional[str] = None
    llm_model: str = "claude-sonnet-5-5"
    groq_key: Optional[str] = None
    groq_model: str = "openai/gpt-oss-20b"
    llm_provider_pref: Optional[str] = None   # "groq" or "anthropic"; default = whichever key is set
    google_key: Optional[str] = None
    opentripmap_key: Optional[str] = None
    database_url: str = "sqlite:///trips.db"
    auth_token: Optional[str] = None       # if set, the HTTP API requires "Authorization: Bearer <token>"
    allowed_origin: Optional[str] = None   # CORS origin for a browser front end, e.g. https://myapp.vercel.app
    port: int = 8000

    @property
    def llm_provider(self) -> Optional[str]:
        have = {"anthropic": bool(self.anthropic_key), "groq": bool(self.groq_key)}
        if self.llm_provider_pref in have and have[self.llm_provider_pref]:
            return self.llm_provider_pref
        return "anthropic" if have["anthropic"] else ("groq" if have["groq"] else None)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        if env is None:
            load_dotenv()
            env = os.environ
        return cls(
            anthropic_key=env.get("ANTHROPIC_API_KEY") or None,
            llm_model=env.get("LLM_MODEL") or cls.llm_model,
            groq_key=env.get("GROQ_API_KEY") or None,
            groq_model=env.get("GROQ_MODEL") or cls.groq_model,
            llm_provider_pref=(env.get("LLM_PROVIDER") or "").lower() or None,
            google_key=env.get("GOOGLE_PLACES_API_KEY") or None,
            opentripmap_key=env.get("OPENTRIPMAP_API_KEY") or None,
            database_url=env.get("DATABASE_URL") or cls.database_url,
            auth_token=env.get("API_AUTH_TOKEN") or None,
            allowed_origin=env.get("ALLOWED_ORIGIN") or None,
            port=int(env.get("PORT") or 8000),
        )
