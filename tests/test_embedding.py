"""Embedding provider 兼容 / 语义召回测试。"""
import asyncio
from types import SimpleNamespace

import pytest

from provider_router import (
    cosine_similarity,
    embed_texts,
    get_embedding_provider,
    rank_indices_by_vector,
)


def _vec(text, dim=4):
    """固定维度伪向量，保证不同文本长度都返回相同维度。"""
    v = [0.0] * dim
    for i, ch in enumerate(text):
        v[i % dim] += float(ord(ch) % 4 + 1)
    return v


class FakeEmbedding:
    """AstrBot 原生 EmbeddingProvider 形态：get_embeddings / get_embedding / get_dim。"""

    def __init__(self, dim=4):
        self.dim = dim

    def get_embeddings(self, texts):
        return [_vec(t, self.dim) for t in texts]

    def get_embedding(self, text):
        return _vec(text, self.dim)

    def get_dim(self):
        return self.dim


class FakeEmbeddingAsync:
    """社区 astrbot_plugin_embedding_adapter 形态：get_embeddings_async。"""

    def get_embeddings_async(self, texts):
        return [_vec(t, 4) for t in texts]


class FakeCtx:
    def __init__(self, emb=None, by_id=None):
        self._emb = emb
        self._by_id = by_id or {}

    async def get_provider_by_id(self, pid):
        return self._by_id.get(pid)

    def get_registered_star(self, name):
        return None

    @property
    def provider_manager(self):
        pm = SimpleNamespace()
        pm.embedding_provider_insts = [self._emb] if self._emb else []
        pm.embed_provider_insts = []
        return pm


def _run(coro):
    return asyncio.run(coro)


# ---------- 纯函数 ----------
def test_cosine_identical():
    v = [1.0, 2.0, 3.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_dim_mismatch():
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0


def test_cosine_zero_vector():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_rank_indices_by_vector():
    q = [1.0, 0.0, 0.0]
    vecs = [
        [0.9, 0.0, 0.0],  # mem0 最相关
        [0.0, 1.0, 0.0],  # mem1 不相关
        [0.8, 0.0, 0.0],  # mem2 次相关
    ]
    assert rank_indices_by_vector(q, vecs, 3) == [0, 2, 1]


# ---------- provider 获取（多来源兼容） ----------
def test_get_embedding_provider_by_id():
    emb = FakeEmbedding()
    ctx = FakeCtx(by_id={"myemb": emb})
    assert _run(get_embedding_provider(ctx, "myemb")) is emb


def test_get_embedding_provider_native_inst():
    emb = FakeEmbedding()
    ctx = FakeCtx(emb=emb)
    assert _run(get_embedding_provider(ctx)) is emb


def test_get_embedding_provider_adapter():
    emb = FakeEmbeddingAsync()
    ctx = SimpleNamespace()
    ctx.get_provider_by_id = None
    ctx.get_registered_star = (
        lambda name: emb if name == "astrbot_plugin_embedding_adapter" else None
    )
    ctx.provider_manager = SimpleNamespace(embedding_provider_insts=[], embed_provider_insts=[])
    assert _run(get_embedding_provider(ctx)) is emb


def test_get_embedding_provider_none():
    ctx = FakeCtx()
    assert _run(get_embedding_provider(ctx)) is None


# ---------- embed_texts（批量 / 单条兜底 / async） ----------
def test_embed_texts_batch():
    out = _run(embed_texts(FakeEmbedding(), ["a", "bb"]))
    assert len(out) == 2 and len(out[0]) == 4


def test_embed_texts_single_fallback():
    class OnlySingle:
        def get_embedding(self, text):
            return [float(ord(c) % 4 + 1) for c in text]

    out = _run(embed_texts(OnlySingle(), ["x", "y", "z"]))
    assert len(out) == 3


def test_embed_texts_async():
    out = _run(embed_texts(FakeEmbeddingAsync(), ["a", "b"]))
    assert len(out) == 2


def test_embed_texts_no_provider():
    assert _run(embed_texts(None, ["a"])) == []


# ---------- 语义召回链路（组合验证 core._recall_semantic 核心逻辑） ----------
def test_semantic_recall_ranking():
    """模拟 _recall_semantic：query 向量命中语义最相关的两条记忆。"""
    mems = [SimpleNamespace(content=str(i)) for i in range(3)]
    qvec = [1.0, 0.0, 0.0]
    vecs = [
        [0.9, 0.0, 0.0],  # mem0 最相关
        [0.0, 1.0, 0.0],  # mem1 不相关
        [0.8, 0.0, 0.0],  # mem2 次相关
    ]
    idxs = rank_indices_by_vector(qvec, vecs, 3)
    ranked = [mems[i] for i in idxs]
    assert [m.content for m in ranked] == ["0", "2", "1"]
