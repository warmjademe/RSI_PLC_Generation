from .embeddings import HuggingFaceMeanPoolEmbedder, SentenceTransformerEmbedder


def make_embedder(config):
    kind = config.get("kind")
    if not config.get("revision") or config["revision"] in {"main", "REPLACE_WITH_IMMUTABLE_COMMIT"}:
        raise ValueError("A pinned encoder revision is required; no silent encoder fallback")
    arguments = {k: config[k] for k in ("model_name", "revision", "batch_size", "device") if k in config}
    if kind == "sentence_transformer":
        return SentenceTransformerEmbedder(**arguments)
    if kind == "mean_pool":
        if "maximum_tokens" in config:
            arguments["maximum_tokens"] = config["maximum_tokens"]
        return HuggingFaceMeanPoolEmbedder(**arguments)
    raise ValueError("Unsupported encoder kind")
