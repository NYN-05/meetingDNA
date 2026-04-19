import os
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv

load_dotenv(override=True)


def _normalize_neo4j_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme in {"neo4j", "neo4j+s", "neo4j+ssc"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        scheme = parsed.scheme.replace("neo4j", "bolt", 1)
        return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return uri

class Config:
    NEO4J_ENABLED = os.getenv("NEO4J_ENABLED", "false").lower() == "true"
    NEO4J_URI = _normalize_neo4j_uri(os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./data/chromadb")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:31b-cloud")
    OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))

config = Config()
