from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parent.parent.parent
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_runtime_no_longer_exposes_rag_modules_or_config() -> None:
    """测试运行时入口不再依赖 RAG / Chroma / embedding / rerank。"""
    import app.config as config
    import app.services.trip_service  # noqa: F401

    assert "app.rag" not in sys.modules
    assert "app.agents.tools.rag_tool" not in sys.modules
    assert not hasattr(config, "CHROMA_DB_DIR")
    assert not hasattr(config, "CHROMA_COLLECTION_NAME")
    assert not hasattr(config, "EMBEDDING_MODEL")
    assert not hasattr(config, "RERANK_MODEL")


def test_requirements_do_not_include_rag_vector_dependencies() -> None:
    """测试后端依赖中已经移除 RAG 向量库重依赖。"""
    requirements = (REPO_ROOT / "backend" / "requirements.txt").read_text()

    assert "chromadb" not in requirements
    assert "langchain-community" not in requirements
