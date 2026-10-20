# Long-term memory

## Scope

The application supports the following local workflow:

1. Initialize an empty PostgreSQL database and its pgvector schema.
2. Define a namespace by inserting its first record with the configured embedding model.
3. Manually insert or replace text; generate a vector for every write.
4. Retrieve records through the `memory_search` MCP tool.

Agent-facing writes, automatic conversation capture, document chunking and hybrid
retrieval are outside the current scope. The agent receives two tools from one
`rag-mcp` server: `web_search` and `memory_search`.

## Modules

| File | Responsibility |
| --- | --- |
| `src/rag_mcp/cli.py` | Route the single command: setup, put, search or MCP serving |
| `src/rag_mcp/config.py` | Load the shared configuration file |
| `src/rag_mcp/server.py` | Create and run the shared MCP server |
| `src/rag_mcp/tools.py` | Register web search and delegate memory registration |
| `src/rag_mcp/memory/tools.py` | Define the memory tool interface |
| `src/rag_mcp/memory/retrieval.py` | Validate queries and execute read-only vector search |
| `src/rag_mcp/memory/embeddings.py` | Call an embedding endpoint and validate vectors |
| `src/rag_mcp/memory/admin.py` | Initialize tables/logins and manually insert or replace records |
| `src/rag_mcp/memory/setup.py` | Prepare the local PostgreSQL and Ollama services |
| `src/rag_mcp/memory/resources/` | Packaged schema and Docker Compose configuration |
| `evaluation/memory/` | Standalone smoke checks and a disposable test runner |

The schema and Compose file are runtime resources packaged with the memory module.
They are required by `setup`, including when installed as a wheel; they are separate
from the Python application logic.

`pyproject.toml` is the package manifest. It declares dependencies, build rules and
`rag-mcp = rag_mcp.cli:main`, which creates the executable command during installation.
It is required even though it is not Python source code.

## Setup and use

Run `setup.ps1` from the project root. It creates the persistent application runtime
in `.local/runtime`, installs the package and invokes `rag-mcp setup`. Existing
configuration values are preserved. PostgreSQL and Ollama listen on localhost;
records and model files persist in separate Docker volumes.

The shared `.env` contains a reader connection for retrieval and an administrator
connection for explicit setup/writes. Setup generates local passwords, creates the
reader role, downloads BGE-M3 and records its model digest. Add a Tavily API key to
the same file for web search. The runtime is retained for agent use; it is distinct
from the temporary environments created by the test script.

See the [project README](../README.md) for the single MCP client configuration and
command examples. No second MCP command or separate per-tool configuration is required.

### Manual insertion and replacement

`rag-mcp put --namespace NAME --content TEXT` creates a record and returns its UUID
and version. `--file PATH` reads UTF-8 text instead. `--metadata` accepts a JSON object;
`--source` records a source URL.

Supply `--id UUID` to replace a record. Replacement regenerates the embedding,
increments the version, updates timestamps and replaces metadata/source along with
content. Omitted metadata/source become empty/null. An ID cannot be moved to another
namespace. The text and vector are committed together; an embedding failure leaves
the previous record intact. Manual replacement is not a concurrent editing protocol.

The first write creates the namespace with its model configuration. Model changes
require a new namespace and regenerated vectors, even when dimensions match.

### Retrieval contract

| Argument | Required | Default | Constraint |
| --- | --- | --- | --- |
| `namespace` | yes | - | Existing namespace; 1–100 characters |
| `query` | yes | - | Non-empty text; up to 8,000 characters |
| `limit` | no | `5` | 1–20 |
| `metadata_filter` | no | `null` | Top-level scalar equality filters, combined with AND |

The response contains `namespace`, `query` and `matches`. Each match includes `id`,
`content`, `metadata`, `source_url`, `created_at`, `updated_at`, `version` and `distance`.
Lower cosine distance means closer vectors, not factual confidence. No matches
returns an empty list; invalid configuration and service failures return errors.

## Implementation

**Storage.** `memory_spaces` records the embedding configuration for each namespace.
`memories` stores original text, metadata, vectors and record lifecycle fields.
Foreign keys, checks and triggers reject invalid dimensions and zero vectors.
Namespace names organize records; they do not authenticate users.

**Embedding.** BGE-M3 is a multilingual model with 1024-dimensional dense output,
not a universal embedding format. Ollama provides the compatible `/embeddings`
HTTP interface, so Python does not need PyTorch, FlagEmbedding or an Ollama SDK.
Only dense retrieval is used; sparse and multi-vector BGE outputs are not enabled.
The pinned Ollama image and recorded model digest identify the tested configuration.

**Search.** A materialized candidate set filters namespace, deletion status and
metadata before pgvector calculates `<=>` cosine distances. A read-only repeatable-read
transaction keeps namespace configuration and records consistent. Bound parameters
separate input values from SQL. This version uses exact search without HNSW indexes.

**Tool boundary.** The MCP wrapper exposes the normal retrieval function. The model
chooses a tool, its host executes the function, and structured results are returned
to the model. Retrieved text remains data, not executable instructions.

## Verification

During development, a local `bge-demo` namespace was populated with three records about
database storage, web search and updating embeddings. These records are not shipped
with the repository; setup creates an empty database on a fresh installation.
Real BGE-M3 vectors were generated and three
Chinese paraphrase queries each ranked their corresponding record first. This is a
small functional demonstration, not a retrieval-quality benchmark.

Verified on 2026-10-19: the single stdio server advertised both tools and answered
four real BGE queries, including a manually replaced record. Its vector changed
and version increased to 2. The web-search regression suite passed 69 checks;
the three database smoke checks passed from a clean, non-editable installation.

The standalone checks remain limited to filtering/ranking with fixed vectors,
empty results and blank-query validation:

```powershell
.\evaluation\memory\run.ps1
```

The runner builds a temporary Python environment, installs the application, starts
an isolated pgvector container, runs the checks, and removes its environment,
cache, records, container and temporary volume in `finally`. It does not use the
persistent application database or download another BGE model. Forced termination
can prevent cleanup. Docker Desktop and cached images remain installed.

## Dependencies and licenses

Direct Python dependencies are mcp (MIT), httpx (BSD-3-Clause), pydantic (MIT) and
psycopg[binary] (LGPL-3.0-only). No third-party project source is vendored.
[evaluation/memory/requirements.example.txt](../evaluation/memory/requirements.example.txt)
records the runtime versions and transitive dependencies used in this environment.

BGE-M3 and Ollama are MIT-licensed; PostgreSQL and pgvector use the PostgreSQL
License. Docker Desktop has its own subscription agreement. Model weights and
container images live in Docker-managed storage, not in the source repository.

References: [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3),
[Ollama model](https://ollama.com/library/bge-m3),
[Ollama compatibility](https://docs.ollama.com/api/openai-compatibility).
