"""YamlWatcher observes only memory/**/*.yaml with debounce ≥300ms."""

import time

import pytest


@pytest.mark.asyncio
async def test_watch_only_yaml(tmp_path):
    from rsi_boot.memory.watch import YamlWatcher

    seen: list[str] = []
    w = YamlWatcher(tmp_path / ".rsi" / "memory", on_change=lambda p: seen.append(p.name))
    (tmp_path / ".rsi" / "memory" / "prohibitions").mkdir(parents=True)
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
    await w.poll_once()
    assert "README.md" not in seen


@pytest.mark.asyncio
async def test_watch_sees_yaml_under_memory(tmp_path):
    from rsi_boot.memory.watch import YamlWatcher

    seen: list[str] = []
    memory = tmp_path / ".rsi" / "memory"
    (memory / "prohibitions").mkdir(parents=True)
    w = YamlWatcher(memory, on_change=lambda p: seen.append(p.name))
    await w.poll_once()
    (memory / "prohibitions" / "no-star.yaml").write_text("id: a\n", encoding="utf-8")
    await w.poll_once()
    assert "no-star.yaml" in seen


@pytest.mark.asyncio
async def test_watch_ignores_md_under_memory(tmp_path):
    from rsi_boot.memory.watch import YamlWatcher

    seen: list[str] = []
    memory = tmp_path / ".rsi" / "memory"
    (memory / "prohibitions").mkdir(parents=True)
    w = YamlWatcher(memory, on_change=lambda p: seen.append(p.name))
    await w.poll_once()
    (memory / "prohibitions" / "notes.md").write_text("# notes", encoding="utf-8")
    await w.poll_once()
    assert "notes.md" not in seen


def test_watch_debounce_at_least_300ms():
    from rsi_boot.memory.watch import YamlWatcher

    w = YamlWatcher
    debounce = getattr(w, "DEBOUNCE_MS", None)
    if debounce is None:
        instance = w.__new__(w)
        debounce = getattr(instance, "debounce_ms", None) or getattr(instance, "DEBOUNCE_MS", None)
    if debounce is None:
        # Class-level default used by constructor
        import inspect

        sig = inspect.signature(w.__init__)
        param = sig.parameters.get("debounce_ms")
        debounce = param.default if param is not None else None
    assert debounce is not None
    assert float(debounce) >= 300
