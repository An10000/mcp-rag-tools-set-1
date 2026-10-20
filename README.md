# mcp-rag-tools-set-1

MCP tools for RAG agents. One `rag-mcp` server exposes both tools:

| Tool | Description |
| --- | --- |
| `web_search` | Search the public web via [Tavily](https://tavily.com); return titles, URLs and snippets. |
| `memory_search` | Search stored text in PostgreSQL + pgvector; return content, metadata, sources and cosine distance. |

## Setup

Requires Python 3.10+ and Docker Desktop with its Linux engine running.
From the project root on Windows:

```powershell
.\setup.ps1
```

Setup installs the application into `.local/runtime`, starts PostgreSQL and Ollama,
downloads BGE-M3, initializes the schema and read-only login, and generates `.env`
with local credentials and the model digest. First-time setup requires network
access; the BGE-M3 model download is approximately 1.2 GB, in addition to container images.
Database records and model weights persist in Docker volumes.

Set `TAVILY_API_KEY` in `.env` to enable web search. Memory retrieval works without it.
The application runtime and `.env` are local files excluded from Git.

For an existing Python environment, the equivalent commands are:

```bash
pip install -e .
rag-mcp setup
```

## Manual data management

Use the same application to insert a note. The vector is generated automatically:

```powershell
.\.local\runtime\Scripts\rag-mcp.exe put --namespace notes --content "项目使用 PostgreSQL 和 pgvector 保存长期记忆。"
```

The command returns a record ID. To replace its content, pass that ID:

```powershell
.\.local\runtime\Scripts\rag-mcp.exe put --namespace notes --id RECORD_UUID --content "更新后的项目说明。"
```

Updates recompute the embedding and increment the version. `put` replaces the full
record; omitted metadata and source URL become empty/null. `--file note.txt` can
replace `--content` for UTF-8 text files. These are manual commands, not agent write tools.

Query through the MCP tool from the terminal:

```powershell
.\.local\runtime\Scripts\rag-mcp.exe search --namespace notes "项目资料存在哪里？"
```

## Agent connection

Configure one MCP server:

```json
{
  "mcpServers": {
    "rag-mcp": {
      "command": "C:\\path\\to\\project\\.local\\runtime\\Scripts\\rag-mcp.exe",
      "env": {
        "RAG_CONFIG": "C:\\path\\to\\project\\.env"
      }
    }
  }
}
```

With no subcommand, `rag-mcp` serves both tools over stdio. `RAG_CONFIG` selects the
configuration file regardless of the client's working directory; the default is
`.env` in the current directory. Existing process environment variables take precedence.

## Configuration

Setup fills the database and BGE settings. `.env.example` documents the shared configuration.

| Variable | Default / setup value | Description |
| --- | --- | --- |
| `TAVILY_API_KEY` | empty | Required for web search |
| `TAVILY_TIMEOUT` | `15` | Tavily request timeout in seconds |
| `TAVILY_SEARCH_URL` | Tavily Search API | Optional search endpoint override |
| `DATABASE_URL` | generated reader login | Read-only retrieval connection |
| `DATABASE_ADMIN_URL` | generated administrator login | Manual setup and writes only |
| `EMBEDDING_BASE_URL` | `http://127.0.0.1:11434/v1` | Compatible embedding endpoint |
| `EMBEDDING_MODEL` | `bge-m3` | Model used for documents and queries |
| `EMBEDDING_REVISION` | model digest | Namespace compatibility identifier |
| `EMBEDDING_DIMENSIONS` | `1024` | Required output dimensions |
| `EMBEDDING_TIMEOUT` | `120` | Local embedding request timeout in seconds |

BGE-M3 is a model, not a storage format. This implementation uses its dense vectors
for cosine retrieval. Stored records and queries must use the same embedding configuration.
See [memory design and usage](docs/memory.md) for requirements, module responsibilities
and tool inputs/outputs.

## Tests

The database smoke checks create and clean up a temporary Python environment,
PostgreSQL container and sample records:

```powershell
.\evaluation\memory\run.ps1
```

They use fixed embeddings and do not download another model. Docker Desktop and
its image cache remain installed. The persistent application database is not used.

Web-search tests mock the Tavily API and require no API key:

```bash
pip install -e ".[dev]"
pytest evaluation/web_search
```

## Project layout

- `src/rag_mcp/`: application code; `memory/` groups memory functions and packaged schema/deployment resources.
- `docs/memory.md`: memory requirements, setup details and implementation explanation.
- `evaluation/`: separate test code and disposable test scripts.
- `pyproject.toml`: package metadata, dependencies, build configuration and the `rag-mcp` command entry point.
