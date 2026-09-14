"""YamlWatcher observes only memory/**/*.yaml with debounce ≥300ms."""

import asyncio
import inspect

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


@pytest.mark.asyncio
async def test_watch_rewrites_inject_when_memory_yaml_changes(tmp_path):
    from rsi_boot.__main__ import on_memory_yaml_change
    from rsi_boot.memory.watch import YamlWatcher

    rewritten: list[str] = []
    invalidated = {"n": 0}

    class _Injector:
        async def rewrite(self, project_id):
            rewritten.append(project_id)

    class _Runtime:
        project_id = "proj-watch"
        injector = _Injector()

        def invalidate_index(self):
            invalidated["n"] += 1

    runtime = _Runtime()
    memory = tmp_path / ".rsi" / "memory"
    (memory / "prohibitions").mkdir(parents=True)
    watcher = YamlWatcher(memory, on_change=lambda p: on_memory_yaml_change(runtime, p))
    await watcher.poll_once()
    (memory / "prohibitions" / "no-star.yaml").write_text("id: a\n", encoding="utf-8")
    await watcher.poll_once()
    await asyncio.sleep(0)
    assert invalidated["n"] >= 1
    assert rewritten == ["proj-watch"]


def test_run_serve_watch_rewrites_inject():
    from rsi_boot import __main__

    src = inspect.getsource(__main__.run_serve)
    assert "on_memory_yaml_change" in src
    helper = inspect.getsource(__main__.on_memory_yaml_change)
    assert "invalidate_index" in helper
    assert "rewrite" in helper
