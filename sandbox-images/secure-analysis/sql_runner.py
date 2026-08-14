#!/usr/bin/env python
"""secure-analysis 镜像内置 SQL runner：在 /project 上执行 DuckDB 查询。

用法：``python dataharness-sql-runner.py <query.sql>``

- 把 /project 下受支持的数据文件（parquet/csv/json）注册为以文件主干命名的表；
  非标识符文件名跳过并写入 stderr，避免把任意路径暴露给 SQL。
- 查询结果以 CSV 写到 stdout（stdout 是 Host 的唯一输出载荷）；schema/statistics
  写入 ``<query.sql>.schema.json`` sidecar，由 Host 包装层有界读取。
- 本脚本只读 /project，任何写操作都来自查询本身对临时表的操作。
"""

from __future__ import annotations

import json
import os
import sys

import duckdb
import pyarrow.csv
import pyarrow.parquet

_SUPPORTED = (".parquet", ".csv", ".json")
_PROJECT = "/project"


def _register_project_files(con: duckdb.DuckDBPyConnection) -> list[str]:
    """注册 /project 数据文件为表；返回被跳过的非标识符文件列表。"""
    skipped: list[str] = []
    if not os.path.isdir(_PROJECT):
        return skipped
    for name in sorted(os.listdir(_PROJECT)):
        full = os.path.join(_PROJECT, name)
        stem, ext = os.path.splitext(name)
        if ext.lower() not in _SUPPORTED or not os.path.isfile(full):
            continue
        if not stem.isidentifier():
            skipped.append(name)
            continue
        try:
            if ext.lower() == ".parquet":
                table = pyarrow.parquet.read_table(full)
            elif ext.lower() == ".csv":
                table = pyarrow.csv.read_csv(full)
            else:
                table = pyarrow.json.read_json(full)
        except Exception as error:  # noqa: BLE001 -- 单文件失败不影响其他表
            skipped.append(f"{name} ({type(error).__name__})")
            continue
        con.register(stem, table)
    return skipped


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: dataharness-sql-runner.py <query.sql>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    with open(path, encoding="utf-8") as handle:
        query = handle.read()
    con = duckdb.connect()
    skipped = _register_project_files(con)
    for name in skipped:
        print(f"skipped non-identifier project file: {name}", file=sys.stderr)
    try:
        result = con.execute(query)
    except Exception as error:
        print(f"SQL execution failed: {error}", file=sys.stderr)
        return 1
    description = result.description
    if description is None:
        print("OK")
        return 0
    frame = result.fetchdf()
    print(frame.to_csv(index=False), end="")
    sidecar = path + ".schema.json"
    columns = [{"name": column[0], "type": str(column[1])} for column in description]
    payload = {
        "schema": {"columns": columns},
        "statistics": {"rows": int(len(frame))},
    }
    try:
        with open(sidecar, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        print("failed to write schema sidecar", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
