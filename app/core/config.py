from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    base_dir: Path
    data_dir: Path
    templates_dir: Path
    static_dir: Path
    database_url: str
    secret_key_path: Path
    openai_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "AppConfig":
        base_dir = Path(__file__).resolve().parents[2]
        data_dir = Path(os.getenv("REVIEWOPS_DATA_DIR", base_dir / "data"))
        database_url = os.getenv(
            "REVIEWOPS_DATABASE_URL",
            f"sqlite:///{(data_dir / 'reviewops.db').as_posix()}",
        )
        return cls(
            app_name=os.getenv("REVIEWOPS_APP_NAME", "ReviewOps AI"),
            base_dir=base_dir,
            data_dir=data_dir,
            templates_dir=base_dir / "app" / "templates",
            static_dir=base_dir / "app" / "static",
            database_url=database_url,
            secret_key_path=Path(
                os.getenv("REVIEWOPS_SECRET_KEY_PATH", data_dir / "reviewops.key")
            ),
            openai_timeout_seconds=float(
                os.getenv("REVIEWOPS_OPENAI_TIMEOUT_SECONDS", "90")
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir.mkdir(parents=True, exist_ok=True)

