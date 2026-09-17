"""工作区落点（对齐 Superpowers：一个工作目录一份 `.rsi/`）。

记忆、画像、归档、identity 全部在 `<workspace>/.rsi/`。
`~/.rsi` 只放用户配置（config.yaml / skills），不存项目记忆。
库内 `project_id` 恒为 `local`——隔离靠目录，不靠哈希命名空间。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

#: 单工作区库内的固定命名空间；隔离由 `.rsi/rsi.db` 文件承担
WORKSPACE_PROJECT_ID = "local"

_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

#: Cursor / VS Code 给全局 MCP 注入的工作区（cwd 经常不是仓库根）。
#: 注意 VSCODE_CWD 刻意不在列：它是编辑器进程自身的 cwd（如安装目录），
#: agent 终端会继承到与工作区无关的值，信任它曾把 .rsi 误绑到编辑器目录。
_IDE_WORKSPACE_ENV = (
    "RSI_PROJECT_ROOT",
    "WORKSPACE_FOLDER_PATHS",
    "WORKSPACE_FOLDER",
    "CURSOR_WORKSPACE_ROOT",
    "CURSOR_WORKSPACE",
    "CURSOR_PROJECT_DIR",
    "VSCODE_WORKSPACE",
)


def rsi_home() -> Path:
    """用户级目录：默认 ~/.rsi，可用 RSI_HOME 覆盖（测试/便携）"""
    return Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi")))


def project_rsi_dir(project_root: Path) -> Path:
    return Path(project_root) / ".rsi"


def project_db_path(project_root: Path) -> Path:
    return project_rsi_dir(project_root) / "rsi.db"


def global_db_path() -> Path:
    return rsi_home() / "global.db"


def legacy_shared_db_path() -> Path:
    """v3.1 及更早的跨项目共享库；仅供迁移发现，新写入不得落这里"""
    return rsi_home() / "rsi.db"


def normalize_project_root(path: Path) -> str:
    """跨平台路径指纹：resolve + Windows 下 normcase，避免盘符大小写造成身份漂移"""
    return os.path.normcase(str(Path(path).resolve()))


def derive_project_id(project_root: Path) -> str:
    """工作区库内固定 id。保留函数名供旧调用；路径不再参与计算。"""
    del project_root
    return WORKSPACE_PROJECT_ID


def _user_rsi_dirs() -> set[Path]:
    """用户配置目录，绝不能当成某个项目的 .rsi/"""
    found: set[Path] = set()
    for raw in (rsi_home(), Path.home() / ".rsi"):
        try:
            found.add(raw.resolve())
        except OSError:
            continue
    return found


def _is_workspace_rsi_dir(path: Path) -> bool:
    """`.rsi/` 存在且不是 ~/.rsi 配置目录，才算工作区已认领"""
    root = Path(path)
    rsi = root / ".rsi"
    if not rsi.is_dir():
        return False
    try:
        resolved = rsi.resolve()
        home = Path.home().resolve()
        if resolved in _user_rsi_dirs() or root.resolve() == home:
            return False
    except OSError:
        return False
    return True


def _path_from_uri_or_text(raw: str) -> Optional[Path]:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return None
    if text.startswith("file:"):
        parsed = urlparse(text)
        path = unquote(parsed.path or "")
        if os.name == "nt" and path.startswith("/") and len(path) >= 3 and path[2] == ":":
            path = path[1:]  # /D:/repo → D:/repo
        text = path or unquote(parsed.netloc)
    try:
        candidate = Path(text).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    except OSError:
        return None
    return None


def _split_workspace_paths(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            return [str(x) for x in data if x]
        except json.JSONDecodeError:
            pass
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    return [text]


def _is_unexpanded_placeholder(value: Path | str) -> bool:
    """Cursor 用户级 mcp.json 常把 ${workspaceFolder} 原样传进 argv。"""
    text = str(value)
    if "${" not in text and "%{" not in text:
        return False
    try:
        if Path(text).is_dir():
            return False
    except OSError:
        pass
    return True


def iter_ide_workspace_paths() -> Iterable[Path]:
    """全局安装时 IDE 注入的工作区，优先于进程 cwd。"""
    seen: set[str] = set()
    for key in _IDE_WORKSPACE_ENV:
        raw = os.environ.get(key)
        if not raw:
            continue
        for piece in _split_workspace_paths(raw):
            path = _path_from_uri_or_text(piece)
            if path is None:
                continue
            token = normalize_project_root(path)
            if token in seen:
                continue
            seen.add(token)
            yield path


def resolve_project_root(
    explicit: Optional[Path] = None,
    cwd: Optional[Path] = None,
) -> Path:
    """工作区发现（全局 `rsi` + 每窗口一份 `.rsi/`）：

    1. `--project-root` / 显式路径
    2. IDE 注入（`WORKSPACE_FOLDER_PATHS` / `RSI_PROJECT_ROOT` / …）
    3. 从 cwd 向上已有的项目 `.rsi/`
    4. cwd（排除用户主目录）
    """
    if explicit is not None and not _is_unexpanded_placeholder(explicit):
        return Path(explicit).resolve()
    for ide_root in iter_ide_workspace_paths():
        logger.info("工作区由 IDE 注入环境变量解析：%s（cwd=%s）", ide_root, cwd or Path.cwd())
        return ide_root
    start = Path(cwd or Path.cwd()).resolve()
    try:
        home = Path.home().resolve()
    except OSError:
        home = None
    for candidate in (start, *start.parents):
        if home is not None and candidate == home:
            break
        if _is_workspace_rsi_dir(candidate):
            return candidate
    return start


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    root: str

    def as_dict(self) -> dict[str, str]:
        return {"project_id": self.project_id, "root": self.root}


def _identity_path(project_root: Path) -> Path:
    return project_rsi_dir(project_root) / "identity.json"


def load_identity(project_root: Path) -> Optional[ProjectIdentity]:
    path = _identity_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = str(data.get("project_id") or "")
    if not _PROJECT_ID_RE.match(pid):
        return None
    return ProjectIdentity(project_id=pid, root=str(data.get("root") or Path(project_root).resolve()))


def load_or_create_identity(project_root: Path) -> ProjectIdentity:
    """工作区身份恒为 local；落盘 identity.json 便于人读，不参与跨目录隔离"""
    root = Path(project_root).resolve()
    identity = ProjectIdentity(project_id=WORKSPACE_PROJECT_ID, root=str(root))
    existing = load_identity(root)
    if existing is None or existing.project_id != WORKSPACE_PROJECT_ID or (
        existing.root and os.path.normcase(existing.root) != normalize_project_root(root)
    ):
        _write_identity(root, identity)
    return identity


def _write_identity(project_root: Path, identity: ProjectIdentity) -> None:
    rsi_dir = project_rsi_dir(project_root)
    rsi_dir.mkdir(parents=True, exist_ok=True)
    path = rsi_dir / "identity.json"
    path.write_text(
        json.dumps(identity.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def project_scope(project_root: Path) -> tuple[Path, str]:
    """(项目库路径, 绑定 project_id)；会按需创建 identity.json"""
    root = Path(project_root).resolve()
    identity = load_or_create_identity(root)
    return project_db_path(root), identity.project_id


def bind_project_id(bound: Optional[str], requested: Optional[str]) -> str:
    """进程已绑定项目时强制使用绑定身份；未绑定（单测直调服务）才回落请求值"""
    if bound:
        if requested and str(requested) != bound:
            logger.warning("忽略跨项目 project_id=%s，使用当前工作区 %s", requested, bound)
        return bound
    text = str(requested or "").strip()
    return text or "default"


def tool_project_id(owner: Any, arguments: Optional[dict[str, Any]] = None) -> str:
    """从 Runtime / 已绑定服务读取身份；arguments 仅在未绑定时生效"""
    bound = getattr(owner, "project_id", None) or getattr(owner, "bound_project_id", None)
    requested = None
    if arguments:
        requested = arguments.get("project_id")
    return bind_project_id(bound, requested if requested is None else str(requested))
