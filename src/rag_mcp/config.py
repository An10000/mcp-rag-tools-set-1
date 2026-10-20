"""Shared .env configuration for the command-line application and MCP server."""

import os
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key.strip().isidentifier():
                raise ValueError(f"Invalid configuration entry on line {number}.")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def load_env(path: Path) -> None:
    """Process environment takes precedence over the configuration file."""
    for key, value in read_env(path).items():
        os.environ.setdefault(key, value)


def save_env(path: Path, values: dict[str, str]) -> None:
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Configuration values must be single-line strings.")
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
