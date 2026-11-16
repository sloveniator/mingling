"""stars/apis/_schema.py -- 配置校验复用 _conf_schema.json（建议④）
================================================================
拆分前 ``stars/admin.py::_validate_config`` 手写了一遍类型转换 + 边界 +
enum 校验，与 ``_conf_schema.json``（90 个字段，已声明
``type/minimum/maximum/enum``）构成**两份真值源**——新增配置项要改两处，
漏改就静默失效。

这里改为直接消费 schema：

* :func:`load_schema` —— 模块级缓存（按 mtime 失效）。拆分前每次
  ``api_config`` GET/POST 都 ``open`` + ``json.load`` 一份 572 行 JSON。
* :func:`coerce_by_schema` —— 按 schema 声明统一转换与校验。

新增配置项从此只需改 schema，校验自动生效。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

# _conf_schema.json 位于插件根目录（本文件在 <root>/stars/apis/ 下）
_SCHEMA_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                 "_conf_schema.json")
)

# (mtime, schema)：schema 是磁盘上的静态文件，热更新时按 mtime 失效即可
_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None

# 视为「假」的字符串字面量。表单/查询串里的 "false" 若用 bool() 转换会
# 得到 True——面板走 JSON 时不会踩到，但第三方按 form 提交就会静默反转。
_FALSY_STRINGS = {"", "0", "false", "no", "off", "null", "none"}


def load_schema(*, force_reload: bool = False) -> Dict[str, Any]:
    """读取并缓存 ``_conf_schema.json``；读不到时返回 {}（校验退化为全忽略）。"""
    global _CACHE
    try:
        mtime = os.path.getmtime(_SCHEMA_PATH)
    except OSError:
        return {} if _CACHE is None else _CACHE[1]

    if not force_reload and _CACHE is not None and _CACHE[0] == mtime:
        return _CACHE[1]

    try:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
        if not isinstance(schema, dict):
            schema = {}
    except (OSError, ValueError):
        schema = {}

    _CACHE = (mtime, schema)
    return schema


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_STRINGS
    return bool(value)


def _check_range(key: str, value: float, spec: Dict[str, Any]) -> Optional[str]:
    minv = spec.get("minimum")
    maxv = spec.get("maximum")
    if minv is not None and value < minv:
        return f"{key} 不能小于 {minv}"
    if maxv is not None and value > maxv:
        return f"{key} 不能大于 {maxv}"
    return None


def coerce_by_schema(payload: Dict[str, Any],
                     schema: Optional[Dict[str, Any]] = None
                     ) -> Tuple[Dict[str, Any], List[str]]:
    """按 schema 声明转换 + 校验 payload。

    :returns: ``(coerced, errors)``。``coerced`` 只含 schema 里声明过的键
              （未知键静默忽略，避免面板把任意键写进运行时覆盖层）；
              ``errors`` 非空即视为整体校验失败。
    """
    schema = load_schema() if schema is None else schema
    coerced: Dict[str, Any] = {}
    errors: List[str] = []

    for key, raw in (payload or {}).items():
        spec = schema.get(key)
        if not isinstance(spec, dict):
            continue  # 未知键：忽略
        stype = spec.get("type", "string")
        try:
            if stype == "bool":
                coerced[key] = _to_bool(raw)
            elif stype == "int":
                value: float = int(raw)
                err = _check_range(key, value, spec)
                if err:
                    errors.append(err)
                    continue
                coerced[key] = value
            elif stype == "float":
                value = float(raw)
                err = _check_range(key, value, spec)
                if err:
                    errors.append(err)
                    continue
                coerced[key] = value
            elif stype == "string":
                enum = spec.get("enum")
                if enum and raw not in enum:
                    errors.append(f"{key} 必须是 {enum} 之一")
                    continue
                coerced[key] = str(raw)
            elif stype == "list":
                if isinstance(raw, str):
                    # 面板的多行文本框会以换行/逗号分隔提交
                    items = [s.strip() for s in raw.replace("\n", ",").split(",")]
                    coerced[key] = [s for s in items if s]
                elif isinstance(raw, (list, tuple)):
                    coerced[key] = list(raw)
                else:
                    errors.append(f"{key} 必须是列表")
            else:
                coerced[key] = raw
        except (TypeError, ValueError) as e:
            errors.append(f"{key} 格式错误: {e}")

    return coerced, errors


__all__ = ["coerce_by_schema", "load_schema"]
