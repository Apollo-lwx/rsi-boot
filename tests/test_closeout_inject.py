"""AGENTS hosted block must include the P2 closeout discipline subsection."""

from __future__ import annotations

import pytest

from rsi_boot.injector.targets import AgentsMdTarget, MemoryBundle, MemoryRow, MAX_TOTAL_CHARS


def _hosted_block(text: str) -> str:
    start = text.index(AgentsMdTarget.BEGIN)
    end = text.index(AgentsMdTarget.END) + len(AgentsMdTarget.END)
    return text[start:end]


def _closeout_subsection(block: str) -> str:
    heading = "## 收工纪律"
    idx = block.index(heading)
    tail = block[idx:]
    end = tail.find(AgentsMdTarget.END)
    return tail[:end] if end >= 0 else tail


def test_closeout_strings_in_agents_hosted_block(tmp_path):
    target = AgentsMdTarget(tmp_path)
    target.write(MemoryBundle())
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    block = _hosted_block(text)
    assert "teach_catch" in block
    assert "teach_record" in block
    assert "wrong_action" in block
    assert "correct_fix" in block
    assert "audit_finish" in block
    assert "最终总结照抄 closeout" in block
    subsection = _closeout_subsection(block)
    assert len(subsection) <= 800


def test_closeout_survives_oversized_prohibition_list(tmp_path):
    target = AgentsMdTarget(tmp_path)
    huge = "X" * (MAX_TOTAL_CHARS + 4096)
    target.write(
        MemoryBundle(
            prohibitions=[
                MemoryRow(
                    id="p" * 32,
                    title="超长禁止项",
                    content=huge,
                    content_type="prohibition",
                )
            ]
        )
    )
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    block = _hosted_block(text)
    assert "teach_catch" in block
    assert "teach_record" in block
    assert "audit_finish" in block
    assert "最终总结照抄 closeout" in block


@pytest.mark.asyncio
async def test_closeout_strings_after_injector_rewrite(tmp_path):
    from rsi_boot.injector.rule_injector import RuleInjector
    from rsi_boot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / ".rsi")
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite()
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    block = _hosted_block(text)
    assert "teach_catch" in block
    assert "teach_record" in block
    assert "audit_finish" in block
    assert "最终总结照抄 closeout" in block
    assert len(_closeout_subsection(block)) <= 800
