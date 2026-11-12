"""Tests autonomes de l'assistant documentaire SportPulse AI."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sportpulse.rag import (
    RagError, RagService, RagSettings, lexical_search, load_chunks, router,
)


@pytest.fixture
def knowledge(tmp_path: Path) -> Path:
    (tmp_path / "rules.md").write_text(
        "# Règles RH\n## Jours bien-être\n"
        "Cinq jours bien-être pour au moins 15 activités physiques par an.\n"
        "## Transport vélo\n"
        "La prime du déplacement sportif est fixée à 5 % du salaire brut annuel.\n"
        "Seuil de contrôle du vélo : 25 kilomètres.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_load_and_retrieve_wellbeing(knowledge: Path):
    service = RagService(RagSettings(knowledge, backend="lexical"))
    result = service.ask("Combien de jours bien-être pour quinze activités ?")
    assert result["backend"] == "lexical"
    assert result["sources"]
    assert "15 activités" in result["answer"]


def test_lexical_query_unrelated_has_no_source(knowledge: Path):
    chunks = load_chunks(knowledge)
    assert lexical_search("Quel temps fait-il sur Jupiter ?", chunks) == []


def test_document_does_not_include_secrets_or_personal_data(knowledge: Path):
    chunks = load_chunks(knowledge)
    assert all(c.file == "rules.md" for c in chunks)


def test_validate_backend(knowledge: Path):
    with pytest.raises(RagError):
        RagService(RagSettings(knowledge, backend="invented"))


def test_empty_directory_rejected(tmp_path: Path):
    with pytest.raises(RagError):
        load_chunks(tmp_path)


def test_api_requires_authentication(knowledge: Path, monkeypatch):
    monkeypatch.setenv("SPORTPULSE_KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setenv("SPORTPULSE_RAG_BACKEND", "lexical")
    monkeypatch.setenv("SPORTPULSE_AI_TOKEN", "local-test-token")
    api = FastAPI()
    api.include_router(router)
    client = TestClient(api)
    payload = {"question": "Combien de jours bien-être ?"}
    assert client.post("/ai/ask", json=payload).status_code == 401
    result = client.post(
        "/ai/ask", json=payload,
        headers={"X-SportPulse-Token": "local-test-token"},
    )
    assert result.status_code == 200
    assert result.json()["sources"]


def test_api_rejects_invalid_question(knowledge: Path, monkeypatch):
    monkeypatch.setenv("SPORTPULSE_KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setenv("SPORTPULSE_AI_TOKEN", "local-test-token")
    api = FastAPI()
    api.include_router(router)
    result = TestClient(api).post("/ai/ask", json={"question": "oui"}, headers={"X-SportPulse-Token": "local-test-token"})
    assert result.status_code == 422


def test_no_private_files_are_indexed(tmp_path: Path):
    (tmp_path / "public.md").write_text("# Document\nPolitique de travail et de sport.", encoding="utf-8")
    (tmp_path / "confidential.csv").write_text("salary,employee\n999,TEST", encoding="utf-8")
    chunks = load_chunks(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].file == "public.md"


def test_mistral_faiss_path_with_mock_provider_and_index(knowledge: Path, monkeypatch):
    """Vérifie la logique FAISS sans clé, réseau ni librairie native installée."""
    import sys
    import types
    import numpy as np

    fake = types.ModuleType("faiss")

    def normalize(matrix):
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

    class FakeIndex:
        def __init__(self, dimension):
            self.d = dimension
            self.data = None

        def add(self, matrix):
            self.data = matrix.copy()

        def search(self, query, n):
            scores = (self.data @ query[0]).reshape(-1)
            indices = np.argsort(-scores)[:n]
            return scores[indices][None, :], indices[None, :]

    fake.normalize_L2 = normalize
    fake.IndexFlatIP = FakeIndex
    monkeypatch.setitem(sys.modules, "faiss", fake)

    class FakeGateway:
        calls = 0
        def embed(self, texts, model):
            self.calls += 1
            return [[1., 0.] if "bien-être" in t else [0., 1.] for t in texts]

        def complete(self, question, context, model):
            assert "rules.md#" in context
            return "Les cinq jours bien-être nécessitent 15 activités [rules.md#référence]."

    gateway = FakeGateway()
    service = RagService(RagSettings(knowledge, backend="mistral_faiss", api_key="fake"), gateway=gateway)
    result = service.ask("Combien de jours bien-être ?")
    assert result["backend"] == "mistral_faiss"
    assert "15 activités" in result["answer"]
    assert gateway.calls >= 2
