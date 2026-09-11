"""前端仓库复验衍生修复：JS/TS import 归一化 + .vue SFC 扫描 + 种子行前缀清理。

实仓（frontend_dbaudit_aigate，Vue3+TS）发现：
- expertise 首位是 "@"（@/ 路径别名被当库名）；@playwright/test 截断为 @playwright
- 243 个 .vue 文件（25% 源码）完全未扫描
- 禁止项种子混入 // 注释与前导引号
"""

from pathlib import Path

from rsi_boot.scanner.code_scanner import aggregate_imports, extract_generic_skeleton, scan_code
from rsi_boot.scanner.rule_seed_scanner import extract_prohibition_lines
from rsi_boot.scanner.signal_discovery import discover_signals

_TS_SOURCE = """import { createApp } from 'vue';
import { createPinia } from 'pinia';
import Antd from 'ant-design-vue';
import { test } from '@playwright/test';
import request from '@/utils/http';
import { helper } from './local-helper';
import dayjs from 'dayjs';

export function setupApp() {}
const render = () => {};
"""


def test_ts_import_scoped_package_and_alias():
    """@scope/pkg 保留完整包名；@/ 别名（自有代码）与相对路径不计入"""
    s = extract_generic_skeleton(_TS_SOURCE, "src/main.ts", ".ts")
    assert "vue" in s.imports
    assert "pinia" in s.imports
    assert "ant-design-vue" in s.imports
    assert "@playwright/test" in s.imports
    assert "dayjs" in s.imports
    for noise in ("@", "@/utils", "@/utils/http", "local-helper"):
        assert noise not in s.imports


def test_js_require_scoped_package():
    source = "const x = require('@vueuse/core');\nconst y = require('express');\n"
    s = extract_generic_skeleton(source, "server.js", ".js")
    assert "@vueuse/core" in s.imports
    assert "express" in s.imports


_VUE_SFC = """<template>
  <a-table :columns="columns" />
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useStore } from 'pinia';
import { fetchList } from '@/api/asset';

const columns = ref([]);
function loadData() {}
</script>

<style scoped lang="less">
.table { color: #333; }
</style>
"""


def test_vue_sfc_scanned():
    """.vue SFC：script 块 import 提取 + 符号提取，language=vue"""
    s = extract_generic_skeleton(_VUE_SFC, "src/views/AssetList.vue", ".vue")
    assert s.language == "vue"
    assert "vue" in s.imports
    assert "pinia" in s.imports
    assert "@" not in s.imports
    assert "loadData" in s.symbols


def test_scan_code_includes_vue_files(tmp_path):
    """scan_code 不跳过 .vue；信号发现把 .vue 计入 code"""
    (tmp_path / "Comp.vue").write_text(_VUE_SFC, encoding="utf-8")
    (tmp_path / "main.ts").write_text(_TS_SOURCE, encoding="utf-8")
    skeletons = scan_code(tmp_path, [tmp_path / "Comp.vue", tmp_path / "main.ts"])
    assert any(s.rel_path.endswith("Comp.vue") for s in skeletons)

    signals = discover_signals(tmp_path, 1024 * 1024)
    code_exts = {p.suffix for p in signals["code"].files}
    assert ".vue" in code_exts


def test_vite_cache_excluded_from_signals(tmp_path):
    """.vite-cache 等前端缓存目录不参与信号发现"""
    cache = tmp_path / ".vite-cache" / "deps"
    cache.mkdir(parents=True)
    (cache / "chunk.js").write_text("export {};", encoding="utf-8")
    (tmp_path / "main.ts").write_text(_TS_SOURCE, encoding="utf-8")
    signals = discover_signals(tmp_path, 1024 * 1024)
    assert all(".vite-cache" not in str(p) for p in signals["code"].files)
    assert len(signals["code"].files) == 1


def test_seed_strips_comment_and_quote_prefixes():
    """// 注释、/* */、前导引号/星号不进入种子标题"""
    text = (
        "// ❌ 禁止直接使用 axios 或其他 HTTP 库\n"
        "/* 避免硬编码颜色值 */\n"
        "\"禁止直接修改现有页面，必须新增页面\"\n"
        "- 正常禁止句式保留\n"
    )
    items = extract_prohibition_lines(text)
    assert any(i.startswith("❌ 禁止直接使用 axios") for i in items)
    assert any(i.startswith("避免硬编码颜色值") for i in items)
    assert any(i.startswith("禁止直接修改现有页面") for i in items)
    assert any(i.startswith("正常禁止句式保留") for i in items)
    assert not any(i.startswith(("//", "/*", '"')) for i in items)
