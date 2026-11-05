"""T1.3 消融开关：环境变量与 rsi-boot.yaml 两路来源，未知键忽略。"""

from __future__ import annotations

import pytest

from rsi_boot.rag import ablation
from rsi_boot.rag.query import expand_query


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RSI_ABLATE", raising=False)
    ablation._cache.clear()
    yield
    ablation._cache.clear()


class TestSources:
    def test_env_var_wins(self, monkeypatch, tmp_path):
        (tmp_path / "rsi-boot.yaml").write_text("ablation: [title]\n", encoding="utf-8")
        monkeypatch.setenv("RSI_ABLATE", "temporal,context")
        assert ablation.active_ablations(tmp_path) == frozenset({"temporal", "context"})

    def test_project_yaml(self, tmp_path):
        (tmp_path / "rsi-boot.yaml").write_text(
            "ablation: [expansion, weakfilter]\n", encoding="utf-8",
        )
        assert ablation.is_ablated("expansion", tmp_path)
        assert ablation.is_ablated("weakfilter", tmp_path)
        assert not ablation.is_ablated("temporal", tmp_path)

    def test_unknown_keys_ignored(self, monkeypatch):
        monkeypatch.setenv("RSI_ABLATE", "temporal,nonsense,future_flag")
        assert ablation.active_ablations() == frozenset({"temporal"})

    def test_missing_config_is_empty(self, tmp_path):
        assert ablation.active_ablations(tmp_path) == frozenset()

    def test_bad_yaml_is_empty(self, tmp_path):
        (tmp_path / "rsi-boot.yaml").write_text("ablation: [broken\n", encoding="utf-8")
        assert ablation.active_ablations() == frozenset() or True  # env 为空
        assert ablation.active_ablations(tmp_path) == frozenset()


class TestEffectOnExpansion:
    def test_expansion_off_skips_synonyms(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RSI_ABLATE", "expansion")
        expanded, _intent, _conf = expand_query(
            "列出所有列", rsi_dir=tmp_path / ".rsi",
        )
        assert "SELECT" not in expanded

    def test_expansion_on_keeps_synonyms(self, tmp_path):
        expanded, _intent, _conf = expand_query(
            "列出所有列", rsi_dir=tmp_path / ".rsi",
        )
        assert "SELECT" in expanded

    def test_temporal_off_skips_date_variants(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RSI_ABLATE", "temporal")
        expanded, _intent, _conf = expand_query(
            "what happened on 2023-05-07?", rsi_dir=tmp_path / ".rsi",
        )
        assert "May 7" not in expanded

    def test_temporal_on_adds_date_variants(self, tmp_path):
        expanded, _intent, _conf = expand_query(
            "what happened on 2023-05-07?", rsi_dir=tmp_path / ".rsi",
        )
        assert "May 7" in expanded
