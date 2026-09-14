"""YAML-backed RuleInjector: official prohibitions only, no convention mdc."""

import pytest


@pytest.mark.asyncio
async def test_inject_prohibition_not_convention(tmp_path):
    from rsi_boot.injector.rule_injector import RuleInjector
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="a"*32, type="prohibition", title="禁止 SELECT *", content="必须列字段"),
        dest=official_dir(store.rsi_dir, "prohibition") / "no-star--aaaaaaaa.yaml",
    )
    store.write(
        MemoryDoc(id="b"*32, type="convention", title="约定用命名参数", content="不用位置参数"),
        dest=official_dir(store.rsi_dir, "convention") / "named--bbbbbbbb.yaml",
    )
    inj = RuleInjector(store=store, project_root=tmp_path)
    await inj.rewrite()
    rules = tmp_path / ".cursor" / "rules"
    assert list(rules.glob("rsi-prohibition-*.mdc"))
    assert "禁止 SELECT *" in next(rules.glob("rsi-prohibition-*.mdc")).read_text(encoding="utf-8")
    assert list(rules.glob("rsi-convention-*.mdc")) == []


def test_cursor_and_agents_skip_conventions_when_flag_off(tmp_path):
    from rsi_boot.injector.targets import (
        AgentsMdTarget,
        CursorRuleTarget,
        MemoryBundle,
        MemoryRow,
    )

    bundle = MemoryBundle(inject_conventions=False)
    bundle.conventions.append(
        MemoryRow(
            id="b" * 32, title="约定用命名参数", content="不用位置参数",
            content_type="convention", domain="database",
        )
    )
    CursorRuleTarget(tmp_path).write(bundle)
    AgentsMdTarget(tmp_path).write(bundle)
    rules = tmp_path / ".cursor" / "rules"
    assert list(rules.glob("rsi-convention-*.mdc")) == []
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "经验约定" not in agents


def test_store_bundle_disables_convention_inject(tmp_path):
    from rsi_boot.injector.rule_injector import RuleInjector
    from rsi_boot.memory.paths import official_dir
    from rsi_boot.memory.store import MemoryStore
    from rsi_boot.memory.types import MemoryDoc

    store = MemoryStore(tmp_path / ".rsi")
    store.write(
        MemoryDoc(id="b" * 32, type="convention", title="约定用命名参数", content="不用位置参数"),
        dest=official_dir(store.rsi_dir, "convention") / "named--bbbbbbbb.yaml",
    )
    bundle = RuleInjector(store=store, project_root=tmp_path)._load_bundle_from_store()
    assert bundle.inject_conventions is False
