from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILE = Path(".env")


def read_env_value(name: str, env_path: Path = DEFAULT_ENV_FILE) -> str:
    """환경 변수 또는 로컬 .env 파일에서 필수 값을 읽습니다."""
    value = os.getenv(name, "").strip()
    if value:
        return value.strip('"\'')
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            if key.strip() == name:
                value = raw_value.strip().strip('"\'')
                if value:
                    return value
    raise ValueError(f"{name}이(가) 설정되지 않았습니다.")


def read_optional_env_value(name: str, env_path: Path = DEFAULT_ENV_FILE) -> str:
    """설정되지 않은 선택 환경 변수는 빈 문자열로 반환합니다."""
    try:
        return read_env_value(name, env_path)
    except ValueError:
        return ""
