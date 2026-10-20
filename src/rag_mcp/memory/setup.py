"""Prepare the local PostgreSQL and Ollama services from packaged resources."""

import asyncio
import os
import secrets
import shutil
import subprocess
from importlib.resources import as_file, files
from pathlib import Path
from urllib.parse import quote

import httpx

from rag_mcp.config import read_env, save_env
from rag_mcp.memory.admin import initialize_database
from rag_mcp.memory.embeddings import embed_query


def setup_local(config_path: Path) -> None:
    values = read_env(config_path)
    defaults = {
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "POSTGRES_READER_PASSWORD": secrets.token_hex(16),
        "POSTGRES_PORT": "5432", "TAVILY_API_KEY": "",
        "EMBEDDING_PROVIDER": "openai-compatible",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:11434/v1",
        "EMBEDDING_MODEL": "bge-m3", "EMBEDDING_DIMENSIONS": "1024",
        "EMBEDDING_REVISION": "pending-model-download", "EMBEDDING_TIMEOUT": "120",
    }
    for key, default in defaults.items():
        values[key] = os.environ.get(key, values.get(key, default))
    port = values["POSTGRES_PORT"]
    values.setdefault("DATABASE_ADMIN_URL", f"postgresql://rag_admin:{quote(values['POSTGRES_PASSWORD'], safe='')}@127.0.0.1:{port}/rag_memory")
    values.setdefault("DATABASE_URL", f"postgresql://rag_reader:{quote(values['POSTGRES_READER_PASSWORD'], safe='')}@127.0.0.1:{port}/rag_memory")
    save_env(config_path, values)
    os.environ.update(values)

    docker = shutil.which("docker")
    if docker is None and os.name == "nt":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe"
        docker = str(candidate) if candidate.is_file() else None
    if not docker:
        raise RuntimeError("Docker is not installed or not on PATH. Install/start Docker Desktop first.")
    process_env = os.environ.copy()
    process_env["PATH"] = str(Path(docker).parent) + os.pathsep + process_env.get("PATH", "")
    resource = files("rag_mcp.memory").joinpath("resources/compose.yaml")
    with as_file(resource) as compose:
        command = [docker, "compose", "--env-file", str(config_path), "-f", str(compose)]
        for arguments in (["up", "-d", "--wait"], ["exec", "-T", "ollama", "ollama", "pull", values["EMBEDDING_MODEL"]]):
            result = subprocess.run(command + arguments, env=process_env, check=False)
            if result.returncode:
                raise RuntimeError("Local service setup failed. Check Docker output above.")
    base = values["EMBEDDING_BASE_URL"].removesuffix("/v1").rstrip("/")
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{base}/api/tags")
        response.raise_for_status()
        name = values["EMBEDDING_MODEL"]
        tagged_name = name if ":" in name else name + ":latest"
        values["EMBEDDING_REVISION"] = next(model["digest"] for model in response.json()["models"] if model["name"] == tagged_name)
    save_env(config_path, values)
    os.environ.update(values)
    asyncio.run(initialize_database())
    vector = asyncio.run(embed_query("检查本地向量服务"))
    print(f"Local PostgreSQL and {name} ready ({len(vector)} dimensions). Config: {config_path}")
