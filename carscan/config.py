"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    env: str = "development"                      # development | production | test
    data_dir: Path = Path("data")
    secret_key: str = ""                          # signs nothing, encrypts API keys at rest
    secure_cookies: bool = False                  # set True behind HTTPS
    session_days: int = 30
    max_upload_mb: int = 15
    allow_registration: bool = True
    host: str = "127.0.0.1"
    port: int = 8077
    default_claude_model: str = "claude-opus-5-5"
    default_openai_model: str = "gpt-5"
    login_attempts_per_10min: int = 10
    # Desktop / personal install: bound to this computer only, signs in automatically.
    local_mode: bool = False
    auto_login: bool = True
    update_check: bool = False
    github_repo: str = "lordjhamb/car-scanner"
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "carscan.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def _load_secret(data_dir: Path, env: str) -> str:
    key = os.environ.get("CARSCAN_SECRET_KEY", "").strip()
    if key:
        return key
    if env == "production":
        raise RuntimeError("CARSCAN_SECRET_KEY must be set in production (see .env.example).")
    # Development convenience: generate once and keep it next to the database.
    path = data_dir / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def load_settings(**overrides) -> Settings:
    env = overrides.pop("env", None) or os.environ.get("CARSCAN_ENV", "development")
    data_dir = Path(overrides.pop("data_dir", None) or os.environ.get("CARSCAN_DATA_DIR", "data")).resolve()
    s = Settings(
        env=env,
        data_dir=data_dir,
        secure_cookies=_bool("CARSCAN_SECURE_COOKIES", env == "production"),
        session_days=int(os.environ.get("CARSCAN_SESSION_DAYS", "30")),
        max_upload_mb=int(os.environ.get("CARSCAN_MAX_UPLOAD_MB", "15")),
        allow_registration=_bool("CARSCAN_ALLOW_REGISTRATION", True),
        host=os.environ.get("CARSCAN_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARSCAN_PORT", "8077")),
        default_claude_model=os.environ.get("CARSCAN_CLAUDE_MODEL", "claude-opus-5-5"),
        default_openai_model=os.environ.get("CARSCAN_OPENAI_MODEL", "gpt-5"),
        local_mode=_bool("CARSCAN_LOCAL_MODE", False),
        auto_login=_bool("CARSCAN_AUTO_LOGIN", True),
        update_check=_bool("CARSCAN_UPDATE_CHECK", False),
        github_repo=os.environ.get("CARSCAN_GITHUB_REPO", "lordjhamb/car-scanner"),
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    if not s.secret_key:
        s.secret_key = _load_secret(s.data_dir, s.env)
    return s
