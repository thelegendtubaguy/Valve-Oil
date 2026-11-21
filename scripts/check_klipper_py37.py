#!/usr/bin/env python3
"""Check that the Klipper extras still load under Python 3.7.

The QIDI Plus 4 runs Klipper in a Python 3.7 virtualenv, so every module under installer/klipper/ must use
3.7 syntax and avoid standard-library features added later. Grammar is checked with ast's feature_version;
the later library features this code base would reach for are matched by name.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KLIPPER_ROOT = REPO_ROOT / "installer/klipper"
TARGET = (3, 7)

# Library features newer than 3.7, as they appear in source.
NEWER_LIBRARY = (
    (re.compile(r"\.remove(?:prefix|suffix)\("), "str.removeprefix/removesuffix (3.9)"),
    (re.compile(r"\bfunctools\.cache\b|from functools import [^\n]*\bcache\b"), "functools.cache (3.9)"),
    (re.compile(r"\bcached_property\b"), "functools.cached_property (3.8)"),
    (re.compile(r"\bmath\.(?:prod|isqrt|dist|comb|perm|lcm|nextafter|ulp)\b"), "math function newer than 3.7"),
    (re.compile(r"\bimport (?:zoneinfo|graphlib)\b|\bfrom (?:zoneinfo|graphlib) import\b"), "module added in 3.9"),
    (re.compile(r"\bimportlib\.metadata\b|from importlib import [^\n]*\bmetadata\b"), "importlib.metadata (3.8)"),
    (re.compile(r"\bshlex\.join\b"), "shlex.join (3.8)"),
    (re.compile(r"\basyncio\.to_thread\b"), "asyncio.to_thread (3.9)"),
    (re.compile(r"\bzip\([^)\n]*\bstrict="), "zip(strict=) (3.10)"),
    (re.compile(r"\.bit_count\(\)"), "int.bit_count (3.10)"),
    (re.compile(r"from typing import [^\n]*\b(?:Literal|Protocol|TypedDict|Final)\b"), "typing name added in 3.8"),
    (re.compile(r"f(?:\"|')[^\"'\n]*\{[^}\n]*=(?:![rsa])?(?::[^}\n]*)?\}"), "f-string = specifier (3.8)"),
)
BUILTIN_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}


def _annotations(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def problems(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path), feature_version=TARGET)
    except SyntaxError as error:
        return [f"line {error.lineno}: {error.msg}"]
    found = [why for pattern, why in NEWER_LIBRARY if pattern.search(source)]
    tree = ast.parse(source, filename=str(path))
    postponed = any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
    if not postponed:
        for annotation in _annotations(tree):
            for node in ast.walk(annotation):
                if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                        and node.value.id in BUILTIN_GENERICS:
                    found.append(f"line {node.lineno}: {node.value.id}[...] annotation needs "
                                 "'from __future__ import annotations' before 3.9")
                    break
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                    found.append(f"line {node.lineno}: X | Y annotation needs "
                                 "'from __future__ import annotations' before 3.10")
                    break
    return found


def main() -> int:
    files = sorted(KLIPPER_ROOT.rglob("*.py"))
    failures = {path: found for path in files if (found := problems(path))}
    for path, found in failures.items():
        for problem in found:
            print(f"{path.relative_to(REPO_ROOT)}: {problem}", file=sys.stderr)
    if failures:
        print(f"{len(failures)} of {len(files)} Klipper modules won't load under Python "
              f"{TARGET[0]}.{TARGET[1]} (the QIDI Plus 4's klippy-env).", file=sys.stderr)
        return 1
    print(f"All {len(files)} Klipper modules use Python {TARGET[0]}.{TARGET[1]} syntax and library features.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
