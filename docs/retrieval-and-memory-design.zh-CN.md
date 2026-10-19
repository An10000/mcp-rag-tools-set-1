# 网页检索与长期记忆设计

状态：方案草案，尚未实现数据库工具。部署目标：本机 Docker PostgreSQL + pgvector。

## 1. 现有网页检索模块

调用链：智能体宿主 / MCP 客户端 → tools.py → web.py → Tavily Search API。

本项目提供工具，不包含聊天模型、智能体决策循环或用户界面。server.py 当前通过 stdio 运行，适合由本机 MCP 客户端启动子进程。远程服务需要另行实现和配置 HTTP 传输、身份认证与部署。

### 输入

| 参数 | 类型 | 约束 | 默认值 |
|---|---|---|---|
| query | string | 必填，去除首尾空白后不能为空 | 无 |
| max_results | integer | 1–10 | 5 |
| include_domains | string[] / null | 可选，最多 20 个；域名规范化、去重 | null |

示例：

```json
{"query":"pgvector cosine distance","max_results":3,"include_domains":["github.com"]}
```

### 输出

业务输出为 SearchResponse，通过 MCP 工具结果返回结构化内容。以下为结构示例，不代表真实搜索结果：

```json
{
  "query": "pgvector cosine distance",
  "results": [
    {
      "title": "Example title",
      "url": "https://example.com/page",
      "snippet": "Cleaned excerpt from a search result.",
      "published_date": null
    }
  ]
}
```

摘要清理后按 1000 字符预算截断，另加省略号；没有匹配时返回空 results。日期是上游提供的字符串，可能为空，不能保证代表最近更新时间。参数错误、配置错误、网络错误以 MCP 工具错误返回，不应被理解为“没有结果”。

当前固定 basic 搜索，不生成 Tavily answer、不返回图片或网页全文，也没有单独的网页读取工具。搜索返回后由调用方模型评估来源、组织答案并引用 URL。不会自动写入数据库。

### 接入与部署

需要 Python >=3.10、安装项目及其依赖、Tavily API key，以及能访问 Tavily API 的网络。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

MCP 客户端配置示例（路径须替换为实际项目绝对路径）：

```json
{
  "mcpServers": {
    "rag-mcp": {
      "command": "C:\\path\\to\\project\\.venv\\Scripts\\rag-mcp.exe",
      "env": {"TAVILY_API_KEY": "YOUR_KEY"}
    }
  }
}
```

可选环境变量：TAVILY_TIMEOUT（默认每次请求 15 秒）、TAVILY_SEARCH_URL（默认官方 Search API）。代码最多重试两次，因此一次工具调用的总耗时可能超过 15 秒。当前代码不自动加载 .env，变量必须由父进程或客户端注入。密钥不应提交到仓库。

兼容性取决于宿主支持 MCP stdio、工具发现与调用、以及当前服务的协议版本。模型品牌不是主要限制。仅支持模型 function calling 的宿主需要桥接；仅有聊天输入的模型接口不能直接运行本工具。Python 程序也可直接调用 rag_mcp.web.web_search，它没有 MCP 依赖。

## 2. 长期记忆的边界

目标：智能体可以跨会话保存、读取、更新和删除短文本记忆，并使用语义搜索找回相关内容。原文与元数据存于 PostgreSQL，pgvector 保存对应 embedding 并进行相似度计算。

pgvector 不生成 embedding。服务需要独立的 embedding 实现，可配置远程 API 或本地多语言模型。第一版将其封装为可替换接口，不绑定聊天模型，也不要求调用方智能体生成向量。

第一版按“单个本地使用者、多个项目空间”设计。多个智能体要共享记忆，需要连接同一个数据库并具有相同 namespace 权限；跨会话记忆也依赖宿主提供工具并引导智能体主动检索。它不会修改模型权重，也不会自动把数据库全部注入上下文。

暂不包含：文件上传解析、大文档分块、自动对话归档、知识图谱、任意 SQL 执行、多人远程服务。

## 3. 候选工具接口

工具统一返回结构化 JSON；时间使用带时区的 ISO 8601，ID 使用 UUID。

| 工具 | 输入 | 输出与行为 |
|---|---|---|
| memory_create | namespace, content, metadata={}, source_url?, idempotency_key? | 返回完整记录；生成 embedding 后在事务内写入 |
| memory_search | namespace, query, limit=5（1–20）, metadata_filter={} | 返回 matches，含 id、原文、元数据、来源、时间、version、distance |
| memory_get | namespace, id | 精确读取；不依赖 embedding 服务 |
| memory_list | namespace, limit=20（1–100）, cursor? | 按 created_at、id 稳定排序分页；不依赖 embedding 服务 |
| memory_update | namespace, id, expected_version, content?, metadata?, source_url? | 内容变化时重算 embedding；版本不一致报冲突 |
| memory_delete | namespace, id, expected_version | 软删除并返回 id、deleted_at；普通读取不再返回 |

约定：namespace 非空，长度不超过 100；content 去除首尾空白后非空，上限先定 8000 字符，另受 embedding 模型 token 限制；metadata 必须是 JSON 对象，序列化大小最多 16 KiB。metadata_filter 第一版仅支持顶层标量字段等值匹配，多个字段按 AND 组合。

metadata_update 采用整对象替换，不做隐式深合并；未传字段保持原值，source_url 显式 null 表示清空。更新至少提交一个可变字段。idempotency_key 同一空间下重复且内容相同返回已有记录，内容不一致时报冲突，避免重试造成重复写入。

memory_search 返回 cosine distance（越小越相关），不命名为“可信度”；第一版不设置跨模型通用阈值。语义相似不保证事实正确，也不保证第一条一定相关。无匹配返回空数组，故障返回错误，不混淆两者。

不向智能体返回 embedding 数组，不提供通用 SQL 工具。工具提示应要求将检索出的记忆视为数据，保留来源与时间，不执行记忆文本中的指令。搜索网页与保存记忆保持独立调用，由智能体选择值得保存的信息。

## 4. 数据结构

### memory_spaces

| 字段 | 类型 | 用途 |
|---|---|---|
| namespace | text primary key | 空间标识，如 project:demo |
| embedding_provider | text not null | 生成向量的实现标识 |
| embedding_model | text not null | 模型标识 |
| embedding_revision | text not null | 模型/配置版本标识，避免同名模型悄然变更 |
| embedding_dimensions | integer not null | 正数，必须与实际返回一致 |
| created_at | timestamptz not null | 服务端创建时间 |

namespace 由受信任配置预先创建。每个 namespace 固定一套 embedding 配置，搜索时使用对应配置；不混用不同向量空间，即使它们维度相同。更换模型通过新空间或明确迁移流程重建所有向量，不能直接覆盖模型名。

### memories

| 字段 | 类型 | 用途 |
|---|---|---|
| id | uuid primary key | 记录 ID |
| namespace | text not null references memory_spaces | 所属空间 |
| content | text not null | 可恢复的原始文本 |
| metadata | jsonb not null default '{}' | 标签、类别、来源说明等 |
| source_url | text nullable | 可选来源 URL |
| embedding | vector not null | 用于语义检索的向量 |
| idempotency_key | text nullable | 调用重试去重键 |
| version | integer not null default 1 | 乐观锁版本，更新和删除递增 |
| created_at | timestamptz not null | 创建时间 |
| updated_at | timestamptz not null | 更新时间 |
| deleted_at | timestamptz nullable | 软删除时间 |

使用不限定维度的 vector 列以容纳不同空间，写入时校验空间配置、向量维度、数值有限性及非零范数；后续迁移增加数据库层一致性约束或触发器。数据库另检查 metadata 是对象、version 和 dimensions 为正数、content 非空。

索引：namespace + created_at + id 的活跃记录分页索引；namespace + idempotency_key 的唯一索引（仅非空键，删除后仍保留幂等语义）。metadata 的 GIN 索引按查询需求加入。

第一版采用空间内精确 cosine 最近邻查询，先按 namespace、deleted_at 和 metadata 过滤，再排序取 limit，便于验证正确性。数据量增大后根据实测加 HNSW。不同维度需要分别建适配维度的索引，不能直接对混合维度列建统一 HNSW；引入近似索引后还要验证过滤条件下的召回率。

所有 get/update/delete 都同时匹配 namespace 和 id；namespace 在本地版只是组织边界，不等于身份认证。日后多人或远程部署必须由服务端认证身份约束可访问空间，不能仅信任工具传入的 namespace。

## 5. 模块与配置

- database.py：连接池、参数化查询、事务、CRUD 和向量检索。
- embeddings.py：embed_documents / embed_query 接口、批量与长度校验、可替换 provider。
- memory.py（新增）：输入输出模型、校验、配置与存储的协调。
- tools.py：新增六个 memory 工具；原 web_search 保持独立可用。
- migrations/（新增）：显式管理建表与后续升级；不允许每次工具调用自动变更 schema。
- compose.yaml（新增）：PostgreSQL + pgvector、健康检查、持久数据卷，端口仅绑定 127.0.0.1。

拟定配置：DATABASE_URL、MEMORY_ENABLED、EMBEDDING_PROVIDER、EMBEDDING_MODEL、EMBEDDING_REVISION、EMBEDDING_DIMENSIONS；远程 provider 另需 EMBEDDING_BASE_URL 与 EMBEDDING_API_KEY。实际模型与维度仍待选择，不在方案阶段默认绑定供应商。

数据库默认在容器内运行，Python MCP 服务在宿主机运行，通过本机映射端口连接。命名卷使数据库容器重建后仍保留数据，但不是备份；需要另有 pg_dump / 恢复流程。设置数据库密码并通过环境变量传入，密钥不入库、不进入 MCP 工具参数。

迁移账号负责启用 vector 扩展和建表，运行账号只持有必要表权限。未启用 memory 时不要求数据库或 embedding 配置；启用后故障应明确报告，不拖垮独立的 web_search。

## 6. 实现与验收顺序

1. 选定 embedding provider、模型和输出维度；中文/英文混合记忆用真实样本验证召回。
2. 加 Docker 配置、版本固定的数据库镜像、迁移和配置示例，验证数据库重启后数据仍在。
3. 实现创建、精确读取、分页、更新、软删除，验证幂等、事务和版本冲突。
4. 实现语义搜索，验证过滤在排序前生效、空间隔离、向量配置不匹配时明确拒绝。
5. 通过 MCP 客户端验证完整流程；embedding 服务失败时不写入半成品记录，更新失败保留旧内容。
6. 回归现有 web_search 测试；数据库测试必须使用真实 pgvector，embedding 在确定性测试中可用桩，另保留真实模型召回评测。

本方案未安装依赖、启动容器或修改现有搜索代码。

## 参考

- MCP Python SDK 架构：https://py.sdk.modelcontextprotocol.io/get-started/first-steps/
- Tavily Search API：https://docs.tavily.com/documentation/api-reference/endpoint/search
- pgvector 官方项目（距离、索引与过滤）：https://github.com/pgvector/pgvector
