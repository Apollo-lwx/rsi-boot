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
