"""T1.1 时间感知检索：日期抽取、查询时间信号、打分提权。"""

from __future__ import annotations

from datetime import date

from rsi_boot.memory.types import MemoryDoc
from rsi_boot.rag.index import build_index, search
from rsi_boot.rag.temporal import (
    extract_iso_dates,
    extract_month_grains,
    parse_query_temporal,
    date_match_score,
)


def _doc(doc_id: str, title: str, content: str) -> MemoryDoc:
    return MemoryDoc(id=doc_id, type="documentation", title=title, content=content)


class TestExtractIsoDates:
    def test_iso_variants(self):
        assert extract_iso_dates("released on 2026-10-28 and 2026/10/29") == [
            "2026-10-28", "2026-10-29",
        ]

    def test_chinese_date(self):
        assert extract_iso_dates("会议定在 2026年3月5日 下午") == ["2026-03-05"]

    def test_english_mdy_and_dmy(self):
        assert "2023-05-07" in extract_iso_dates("on May 7, 2023 they met")
        assert "2023-05-07" in extract_iso_dates("on 7 May 2023 they met")
        assert "2023-05-07" in extract_iso_dates("May 7th, 2023")

    def test_cap_and_dedup(self):
        text = " ".join(f"2026-01-{d:02d}" for d in range(1, 15))
        dates = extract_iso_dates(text)
        assert len(dates) == 8
        assert extract_iso_dates("2026-01-05 2026-01-05") == ["2026-01-05"]

    def test_invalid_date_rejected(self):
        assert extract_iso_dates("2026-13-40") == []


class TestMonthGrains:
    def test_english_month_year(self):
        assert (2023, 5) in extract_month_grains("back in May 2023 it changed")

    def test_chinese_year_month(self):
        assert (2026, 3) in extract_month_grains("2026年3月上线")


class TestParseQueryTemporal:
    REF = date(2023, 8, 15)

    def test_explicit_date(self):
        signal = parse_query_temporal("what happened on 2023-05-07?", self.REF)
        assert signal.is_temporal
        assert "2023-05-07" in signal.exact_dates

    def test_relative_last_week(self):
        signal = parse_query_temporal("what did they discuss last week?", self.REF)
        assert signal.is_temporal
        assert ("2023-08-08", "2023-08-14") in signal.windows

    def test_n_days_ago(self):
        signal = parse_query_temporal("what happened 3 days ago?", self.REF)
        assert signal.is_temporal
        assert signal.windows  # 2023-08-12 ± 半单位宽
        start, end = signal.windows[0]
        assert start <= "2023-08-12" <= end

    def test_chinese_relative(self):
        signal = parse_query_temporal("昨天部署了什么？", self.REF)
        assert ("2023-08-14", "2023-08-14") in signal.windows

    def test_when_question_is_temporal_without_dates(self):
        signal = parse_query_temporal("when did Caroline adopt a dog?", self.REF)
        assert signal.is_temporal
        assert not signal.exact_dates

    def test_plain_question_not_temporal(self):
        signal = parse_query_temporal("what is the capital of France?", self.REF)
        assert not signal.is_temporal
        assert not signal.windows

    def test_month_grain_window(self):
        signal = parse_query_temporal("what happened in May 2023?", self.REF)
        assert ("2023-05-01", "2023-05-31") in signal.windows

    def test_no_reference_still_flags_explicit(self):
        signal = parse_query_temporal("what happened on 2023-05-07?", None)
        assert signal.is_temporal
        assert signal.exact_dates


class TestDateMatchScore:
    def test_exact(self):
        signal = parse_query_temporal("on 2023-05-07?", date(2023, 8, 1))
        assert date_match_score(["2023-05-07"], signal) == 1.0

    def test_window_hit(self):
        signal = parse_query_temporal("last week?", date(2023, 8, 15))
        assert date_match_score(["2023-08-10"], signal) == 0.8

    def test_no_match(self):
        signal = parse_query_temporal("last week", date(2023, 8, 15))
        assert date_match_score(["2022-01-01"], signal) == 0.0

    def test_empty_signal(self):
        signal = parse_query_temporal("capital of France?", date(2023, 8, 15))
        assert date_match_score(["2023-08-10"], signal) == 0.0


class TestSearchTemporalBoost:
    def _index(self):
        docs = [
            _doc("a" * 32, "spa visit May", "[2023-05-02] they went to the spa together"),
            _doc("b" * 32, "spa visit July", "[2023-07-20] they went to the spa again"),
        ]
        return build_index(docs)

    def test_boost_pulls_matching_date_up(self):
        index = self._index()
        plain = search(index, "when did they go to the spa", top_n=5)
        signal = parse_query_temporal(
            "what happened last week at the spa", date(2023, 7, 25),
        )
        boosted = search(
            index, "what happened last week at the spa", top_n=5,
            temporal_signal=signal,
        )
        assert boosted and boosted[0][0] == "b" * 32
        assert plain  # 无信号时也能排，只是顺序可能不同

    def test_no_signal_no_change(self):
        index = self._index()
        base = search(index, "spa visit", top_n=5)
        signal = parse_query_temporal("capital of France?", None)
        same = search(index, "spa visit", top_n=5, temporal_signal=signal)
        assert [d for d, _ in base] == [d for d, _ in same]

    def test_ref_date_is_max_doc_date(self):
        index = self._index()
        assert index["ref_date"] == "2023-07-20"
        assert set(index["doc_dates"]) == {"a" * 32, "b" * 32}

    def test_bare_temporal_boosts_date_rich_doc(self):
        docs = [
            _doc("a" * 32, "dog adoption", "[2023-03-01] Caroline adopted a dog"),
            _doc("b" * 32, "dog adoption", "[2023-03-05] Caroline adopted a dog on May 7, 2023"),
        ]
        index = build_index(docs)
        signal = parse_query_temporal("when did Caroline adopt a dog?", date(2023, 8, 1))
        assert signal.bare
        boosted = search(index, "when did Caroline adopt a dog", top_n=2,
                         temporal_signal=signal)
        assert boosted[0][0] == "b" * 32

    def test_diversify_separates_near_duplicates(self):
        from rsi_boot.rag.index import diversify_ranked

        near_dup = "Caroline adopted a beagle dog at the shelter"
        docs = [
            _doc("a" * 32, "t1", f"{near_dup} last spring"),
            _doc("a1".ljust(32, "1"), "t2", f"context: {near_dup} last spring"),  # a 的上下文复制品
            _doc("b" * 32, "t3", "Melanie painted a sunrise at the beach"),
        ]
        index = build_index(docs)
        ranked = search(index, "beagle dog sunrise beach", top_n=3, drop_weak=False)
        diverse = diversify_ranked(index, ranked)
        ids = [d for d, _ in diverse[:2]]
        # 前两名不应全被近似重复的一对占掉
        assert not ({"a" * 32, "a1".ljust(32, "1")} == set(ids))
        assert diverse[0][0] == ranked[0][0]  # 第一名不动

    def test_title_weight_override(self):
        docs = [
            _doc("a" * 32, "zebra", "unrelated body text"),
            _doc("b" * 32, "unrelated", "zebra in body"),
        ]
        index = build_index(docs)
        boosted = dict(search(index, "zebra", top_n=2))
        flat = dict(search(index, "zebra", top_n=2, title_weight=1.0))
        assert boosted["a" * 32] > flat["a" * 32]
        assert boosted["b" * 32] == flat["b" * 32]  # 正文分不受标题权重影响

    def test_expansion_weight_override(self):
        index = self._index()
        base = search(index, "spa resort", top_n=5, expansion_of="spa")
        equal = search(index, "spa resort", top_n=5, expansion_of="spa",
                       expansion_weight=1.0)
        assert equal[0][1] >= base[0][1]
