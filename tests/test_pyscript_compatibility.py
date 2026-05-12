from __future__ import annotations

import ast
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEMPTAMER_ROOT = REPOSITORY_ROOT / "pyscript" / "apps" / "temptamer"


class _GeneratorUsageVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []
        self._function_stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.violations.append(f"line {node.lineno}: generator expression")
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        function_name = self._function_stack[-1] if self._function_stack else "<module>"
        self.violations.append(f"line {node.lineno}: yield in {function_name}")
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        function_name = self._function_stack[-1] if self._function_stack else "<module>"
        self.violations.append(f"line {node.lineno}: yield from in {function_name}")
        self.generic_visit(node)


class PyScriptCompatibilityTests(unittest.TestCase):
    def test_temptamer_uses_no_generators(self):
        violations_by_file: list[str] = []

        for path in sorted(TEMPTAMER_ROOT.glob("*.py")):
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            visitor = _GeneratorUsageVisitor()
            visitor.visit(module)
            if visitor.violations:
                violations = ", ".join(visitor.violations)
                relative_path = path.relative_to(REPOSITORY_ROOT)
                violations_by_file.append(f"{relative_path}: {violations}")

        self.assertEqual(
            violations_by_file,
            [],
            "PyScript app files must not use generator expressions or yield semantics:\n"
            + "\n".join(violations_by_file),
        )