import pytest
import pytest_asyncio

from rsi_boot.data.migrate import migrate
from rsi_boot.data.sqlite import SQLiteClient


@pytest.fixture(autouse=True)
def _isolate_rsi_home(tmp_path, monkeypatch):
    """防止 build_runtime 写进真实 ~/.rsi；清掉 Cursor 注入的工作区以免污染 resolve"""
    monkeypatch.setenv("RSI_HOME", str(tmp_path / ".rsi-home"))
    for key in (
        "RSI_PROJECT_ROOT", "WORKSPACE_FOLDER_PATHS", "WORKSPACE_FOLDER",
        "CURSOR_WORKSPACE", "CURSOR_PROJECT_DIR", "VSCODE_WORKSPACE", "VSCODE_CWD",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest_asyncio.fixture
async def db(tmp_path):
    client = SQLiteClient(tmp_path / "test.db")
    await migrate(client)
    yield client
    await client.close()


@pytest.fixture
def base_config():
    return {
        "model": {"default": "mock", "timeout_s": 5, "max_retries": 0},
        "retrieval": {"top_n": 5, "token_budget": 2000, "bm25_threshold": 0.6},
        "budget": {"daily_token_cap": 0},
        "feedback": {"secret_key": "test-secret"},
    }
