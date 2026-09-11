"""由 Pydantic 模型导出 JSON Schema 到 schemas/（§2.1 单一事实来源）。

用法：python scripts/export_schemas.py
"""

from __future__ import annotations

import json
from pathlib import Path

from rsi_boot.core.models import Feedback, RecallRequest, RSIResponse

OUT_DIR = Path(__file__).resolve().parent.parent / "schemas"


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    targets = {
        "rsi_recall.schema.json": RecallRequest,
        "rsi_response.schema.json": RSIResponse,
        "rsi_feedback.schema.json": Feedback,
    }
    for filename, model in targets.items():
        schema = model.model_json_schema()
        (OUT_DIR / filename).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已生成 {filename}")


if __name__ == "__main__":
    main()
