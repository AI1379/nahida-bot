# Workspace Sandbox 安全增强

> ⚠️ **当前实现风险提示**：现有 `workspace/sandbox.py` 仅使用简单的路径归一化检查，存在被绕过的风险。当前可支撑可信本地 MVP；在开放不可信插件、远程节点执行或高权限文件工具前，需要升级为更健壮的安全方案。

## 当前实现的局限性

```python
# 当前实现（sandbox.py）- 简单路径检查
normalized = (self.root / candidate).resolve(strict=False)
try:
    normalized.relative_to(self.root)
except ValueError as exc:
    raise WorkspacePathError(...)
```

## 已知绕过风险

1. **符号链接攻击**：攻击者可通过符号链接跳出沙盒边界
2. **硬链接攻击**：硬链接可能指向沙盒外文件
3. **竞态条件（TOCTOU）**：检查与实际操作之间存在时间窗口
4. **特殊文件系统对象**：设备文件、FIFO、socket 等未处理
5. **Unicode/编码绕过**：特殊编码可能绕过路径检查

## 推荐增强方案

### 方案 A：多层防御（推荐）

```python
class SecureWorkspaceSandbox:
    """增强版沙盒实现，采用多层防御策略。"""

    def __init__(self, root: Path, *, max_file_size: int = 10 * 1024 * 1024) -> None:
        self.root = root.resolve(strict=True)
        self.max_file_size = max_file_size
        self._allowed_extensions: set[str] | None = None  # 可选：白名单扩展名

    def resolve_safe_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)

        # 第 1 层：拒绝绝对路径
        if candidate.is_absolute():
            raise WorkspacePathError(f"Absolute paths not allowed: {relative_path}")

        # 第 2 层：规范化并检查边界
        normalized = (self.root / candidate).resolve(strict=False)

        # 第 3 层：防止路径穿越（包括 .. 和编码绕过）
        try:
            normalized.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(f"Path escapes workspace: {relative_path}") from exc

        # 第 4 层：拒绝符号链接（即使指向沙盒内）
        # 在实际操作时检查，避免 TOCTOU
        return normalized

    def _validate_before_operation(self, path: Path, *, for_write: bool = False) -> None:
        """操作前进行实时验证，防止 TOCTOU 攻击。"""
        # 检查是否为符号链接
        if path.is_symlink():
            raise WorkspacePathError(f"Symlinks not allowed: {path}")

        # 检查路径是否仍在沙盒内（实时验证）
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise WorkspacePathError(f"Path escapes workspace after resolution: {path}")

        # 写入操作额外检查
        if for_write:
            # 检查父目录是否为符号链接
            if path.parent.is_symlink():
                raise WorkspacePathError(f"Parent directory is symlink: {path.parent}")

            # 可选：检查文件扩展名白名单
            if self._allowed_extensions and path.suffix.lower() not in self._allowed_extensions:
                raise WorkspacePathError(f"File extension not allowed: {path.suffix}")
```

### 方案 B：使用工业级沙盒库（参考 AstrBot）

考虑引入成熟的沙盒库作为依赖：

| 库 | 特点 | 适用场景 |
|---|------|---------|
| `RestrictedPython` | Python 代码沙盒 | 工具执行隔离 |
| `pyrate-limiter` | 频率限制 | 防止资源滥用 |
| 自研 + `os` 模块底层检查 | 文件系统沙盒 | 当前推荐 |

### 方案 C：系统级隔离（未来扩展）

- **容器隔离**：每个 workspace 运行在独立容器中
- **用户命名空间**：利用 Linux user namespace 隔离
- **seccomp/AppArmor**：限制系统调用

## 实施建议

1. **Phase 2.7**：实现方案 A（多层防御）。这是开放不可信插件、远程执行和文件写工具扩权前的安全闸门，包括：
   - 符号链接检测与拒绝
   - TOCTOU 防护（操作时二次验证）
   - 文件大小限制
   - 可选的扩展名白名单

2. **Phase 5+**：根据 Gateway-Node 和远程执行需求评估方案 C（系统级隔离）

## 测试要求

```python
# 必须覆盖的安全测试用例
def test_sandbox_rejects_symlink_escape()
def test_sandbox_rejects_symlink_inside_workspace()
def test_sandbox_rejects_hardlink_escape()
def test_sandbox_rejects_unicode_bypass()
def test_sandbox_enforces_max_file_size()
def test_sandbox_rejects_device_files()
```
