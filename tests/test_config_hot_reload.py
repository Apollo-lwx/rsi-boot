import asyncio

from rsi_boot.bootstrap import build_runtime


async def test_reload_applies_new_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi"))
    runtime = await build_runtime(db_path=tmp_path / "t.db", project_root=tmp_path)
    try:
        assert runtime.config["retrieval"]["top_n"] == 5

        (tmp_path / "rsi-boot.yaml").write_text("retrieval:\n  top_n: 3\n", encoding="utf-8")
        assert runtime.watcher.reload() is True
        assert runtime.config["retrieval"]["top_n"] == 3
        # 依赖配置的组件已重建
        assert runtime.retriever._top_n == 3
    finally:
        await runtime.close()


async def test_reload_keeps_last_good_on_bad_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi"))
    runtime = await build_runtime(db_path=tmp_path / "t.db", project_root=tmp_path)
    try:
        (tmp_path / "rsi-boot.yaml").write_text("retrieval:\n  top_n: not-a-number\n", encoding="utf-8")
        assert runtime.watcher.reload() is False
        assert runtime.config["retrieval"]["top_n"] == 5  # last-good-wins
    finally:
        await runtime.close()


async def test_watch_loop_detects_change(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi"))
    runtime = await build_runtime(db_path=tmp_path / "t.db", project_root=tmp_path)
    try:
        task = asyncio.create_task(runtime.watcher.watch_loop())
        await asyncio.sleep(0.3)  # 等待监听就绪
        (tmp_path / "rsi-boot.yaml").write_text("budget:\n  daily_token_cap: 1000\n", encoding="utf-8")
        for _ in range(30):
            await asyncio.sleep(0.2)
            if runtime.config["budget"]["daily_token_cap"] == 1000:
                break
        task.cancel()
        assert runtime.config["budget"]["daily_token_cap"] == 1000
    finally:
        await runtime.close()
