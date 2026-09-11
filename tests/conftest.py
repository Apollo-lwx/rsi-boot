import pytest
import pytest_asyncio

from rsi_boot.data.migrate import migrate
from rsi_boot.data.sqlite import SQLiteClient


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
