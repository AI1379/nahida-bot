---
title: "Manifest 清单"
description: 从 nahida_bot_sdk.manifest 自动生成的 API 参考。
---

# Manifest 清单

> **源码路径:** `nahida_bot_sdk.manifest`

Plugin manifest model, YAML parsing, and permission declarations.

---


## 类


### ManifestParseError

Raised when a plugin manifest cannot be read or parsed.

- **基类:** `Exception`


### NetworkPermission

Network access permissions.

- **基类:** `BaseModel`

**属性 (Properties):**

#### outbound

- **返回类型:** `list[str]`

#### inbound

- **返回类型:** `bool`


### FilesystemPermission

Filesystem access permissions.

- **基类:** `BaseModel`

**属性 (Properties):**

#### read

- **返回类型:** `list[str]`

#### write

- **返回类型:** `list[str]`


### MemoryPermission

Memory store access permissions.

- **基类:** `BaseModel`

**属性 (Properties):**

#### read

- **返回类型:** `bool`

#### write

- **返回类型:** `bool`


### SystemPermission

System-level access permissions.

- **基类:** `BaseModel`

**属性 (Properties):**

#### env_vars

- **返回类型:** `list[str]`

#### subprocess

- **返回类型:** `bool`

#### signal_handlers

- **返回类型:** `bool`


### Permissions

Aggregate permission declarations for a plugin.

- **基类:** `BaseModel`

**属性 (Properties):**

#### network

- **返回类型:** `NetworkPermission`

#### filesystem

- **返回类型:** `FilesystemPermission`

#### memory

- **返回类型:** `MemoryPermission`

#### system

- **返回类型:** `SystemPermission`

#### llm_access

- **返回类型:** `bool`


### Capabilities

Capability declarations for a plugin.

- **基类:** `BaseModel`

**属性 (Properties):**

#### tools

- **返回类型:** `list[dict[str, str]]`

#### subscribes_to

- **返回类型:** `list[str]`


### PluginDependency

A plugin dependency declaration.

- **基类:** `BaseModel`

**属性 (Properties):**

#### id

- **返回类型:** `str`

#### version

- **返回类型:** `str`


### PluginManifest

Parsed plugin manifest from plugin.yaml.

- **基类:** `BaseModel`

**属性 (Properties):**

#### id

- **返回类型:** `str`

#### name

- **返回类型:** `str`

#### version

- **返回类型:** `str`

#### description

- **返回类型:** `str`

#### entrypoint

- **返回类型:** `str`

#### nahida_bot_version

- **返回类型:** `str`

#### sdk_version

- **返回类型:** `str`

#### load_phase

- **返回类型:** `Literal['pre-agent', 'post-agent']`

#### permissions

- **返回类型:** `Permissions`

#### capabilities

- **返回类型:** `Capabilities`

#### config

- **返回类型:** `dict[str, Any]`

#### config_schema

- **返回类型:** `dict[str, Any]`

#### depends_on

- **返回类型:** `list[PluginDependency]`


## 函数


### `parse_manifest(yaml_path: Path)`

Parse a plugin.yaml file into a validated PluginManifest.

Args:
    yaml_path: Path to the plugin.yaml file.

Returns:
    Validated PluginManifest instance.

Raises:
    ManifestParseError: If the file cannot be read or parsed.
