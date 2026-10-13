from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Target:
    name: str
    address: str
    username: str
    password: str
    verify: bool | str


@dataclass(frozen=True)
class Config:
    listen_address: str
    listen_port: int
    scrape_timeout: float
    workers: int
    targets: tuple[Target, ...]


def load_config(path: str) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    targets = []
    seen = set()
    for item in raw.get("targets", []):
        name = str(item["name"])
        if name in seen:
            raise ValueError(f"duplicate target name: {name}")
        seen.add(name)
        password = item.get("password")
        if not password and item.get("password_env"):
            password = os.environ.get(str(item["password_env"]))
        if not password:
            raise ValueError(f"target {name}: password/password_env is missing")
        verify: bool | str = item.get("ca_file") or bool(item.get("verify_tls", True))
        targets.append(Target(name, str(item["address"]).rstrip("/"), str(item["username"]), str(password), verify))
    if not targets:
        raise ValueError("at least one target is required")
    workers = int(raw.get("workers", 8))
    if workers < 1:
        raise ValueError("workers must be >= 1")
    return Config(str(raw.get("listen_address", "0.0.0.0")), int(raw.get("listen_port", 9610)),
                  float(raw.get("scrape_timeout_seconds", 25)), workers,
                  tuple(targets))
