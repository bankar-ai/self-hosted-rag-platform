"""CORS settings: which origins the browser-based frontend may call this API from.

Required for any browser SPA calling this API cross-origin (the frontend is deployed to a
different origin, e.g. a Vercel domain, than this backend's own host) -- without it, the
browser blocks every request outright regardless of what the backend itself would allow.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CorsSettings(BaseSettings):
    """Overridable via `CORS_ALLOWED_ORIGINS`, a comma-separated list of allowed origins.

    Defaults to Vite's local dev server origin only, so a deployment that hasn't set this
    explicitly gets a working local-dev experience but no open-by-default cross-origin access.
    """

    model_config = SettingsConfigDict(env_prefix="CORS_")

    allowed_origins: str = "http://localhost:5173"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Split `allowed_origins` on commas into the list `CORSMiddleware` expects."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_cors_settings() -> CorsSettings:
    """Return the process-wide cached `CorsSettings` instance."""
    return CorsSettings()
