-- Installed by rag-mcp setup with the administrator account.
-- No sample records or namespaces are inserted automatically.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.memory_spaces (
    namespace text PRIMARY KEY CHECK (namespace = btrim(namespace) AND length(namespace) BETWEEN 1 AND 100),
    embedding_provider text NOT NULL CHECK (length(btrim(embedding_provider)) > 0),
    embedding_model text NOT NULL CHECK (length(btrim(embedding_model)) > 0),
    embedding_revision text NOT NULL CHECK (length(btrim(embedding_revision)) > 0),
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions BETWEEN 1 AND 16000),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.memories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace text NOT NULL REFERENCES public.memory_spaces(namespace),
    content text NOT NULL CHECK (length(btrim(content)) > 0 AND length(content) <= 8000),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    source_url text,
    embedding vector NOT NULL,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CHECK (vector_norm(embedding) > 0)
);

-- Guard imported records as well as future writes. Lock the space so a config
-- update cannot race a write. pgvector itself rejects non-finite vector values.
CREATE OR REPLACE FUNCTION public.check_memory_dimensions() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE expected integer;
BEGIN
    SELECT embedding_dimensions INTO expected FROM public.memory_spaces
        WHERE namespace = NEW.namespace FOR SHARE;
    IF expected IS NULL OR vector_dims(NEW.embedding) <> expected THEN
        RAISE EXCEPTION 'Memory embedding dimensions do not match its namespace';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS memories_dimensions ON public.memories;
CREATE TRIGGER memories_dimensions BEFORE INSERT OR UPDATE ON public.memories
    FOR EACH ROW EXECUTE FUNCTION public.check_memory_dimensions();

CREATE OR REPLACE FUNCTION public.protect_memory_space() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.embedding_provider, NEW.embedding_model, NEW.embedding_revision, NEW.embedding_dimensions)
        IS DISTINCT FROM
       (OLD.embedding_provider, OLD.embedding_model, OLD.embedding_revision, OLD.embedding_dimensions)
       AND EXISTS (SELECT 1 FROM public.memories WHERE namespace = OLD.namespace) THEN
        RAISE EXCEPTION 'Create a new namespace and re-embed records to change the embedding configuration';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS memory_spaces_config ON public.memory_spaces;
CREATE TRIGGER memory_spaces_config BEFORE UPDATE ON public.memory_spaces
    FOR EACH ROW EXECUTE FUNCTION public.protect_memory_space();

CREATE INDEX IF NOT EXISTS memories_active_namespace ON public.memories(namespace, created_at, id)
    WHERE deleted_at IS NULL;
COMMIT;
