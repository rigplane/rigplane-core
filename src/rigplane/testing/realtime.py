"""Shared static clock-call analysis for the pytest guard and PR timing gate."""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Iterable, Set

_CLOCK_READS = frozenset(
    f"time.{name}"
    for name in (
        "monotonic",
        "perf_counter",
        "time",
        "monotonic_ns",
        "perf_counter_ns",
        "time_ns",
    )
)


def number(node: ast.AST | None) -> float | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = number(node.operand)
        return -value if value is not None and isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    return None


def import_names(nodes: Iterable[ast.AST]) -> dict[str, str]:
    """Local name -> dotted origin for the import statements among ``nodes``."""
    names: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                names[local] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def dotted(func: ast.AST, imports: dict[str, str]) -> str | None:
    """The imported dotted name a call target resolves to (``time.sleep``), if any."""
    if isinstance(func, ast.Name):
        return imports.get(func.id)
    if isinstance(func, ast.Attribute):
        base = dotted(func.value, imports)
        return f"{base}.{func.attr}" if base else None
    return None


def is_virtual(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    imports: dict[str, str],
    *,
    allow_explicit_install: bool = True,
) -> bool:
    """Recognize the fixture, decorator and explicit installation of the loop clock."""
    for decorator in fn.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = dotted(target, imports) or ast.unparse(target)
        if name.endswith("virtual_clock"):
            return True
    if "virtual_clock" in {a.arg for a in fn.args.args + fn.args.kwonlyargs}:
        return True
    return allow_explicit_install and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"on_running_loop", "install"}
        for node in ast.walk(fn)
    )


def timeout_value(call: ast.Call, positional: int | None) -> float | None:
    for keyword in call.keywords:
        if keyword.arg == "timeout":
            return number(keyword.value)
    if positional is not None and len(call.args) > positional:
        return number(call.args[positional])
    return None


def clock_primitive(call: ast.Call, name: str | None) -> str | None:
    """Classify a raw clock read or delay, resolving the zero-delay asyncio yield."""
    if name == "time.sleep":
        return name
    if name == "asyncio.sleep":
        delay = next(
            (k.value for k in call.keywords if k.arg == "delay"),
            call.args[0] if call.args else None,
        )
        return name if number(delay) != 0 else None
    return "clock-read" if name in _CLOCK_READS else None


def realtime_markers(source: str) -> dict[int, str]:
    """Return the line and reason of each real-time exemption comment."""
    if "# real-time:" not in source:
        return {}
    return {
        token.start[0]: reason
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
        and token.string.startswith("# real-time:")
        and (reason := token.string.removeprefix("# real-time:").strip())
    }


def realtime_lines(source: str) -> set[int]:
    """Return lines with a Python comment giving a reason for using real time."""
    return set(realtime_markers(source))


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_CURRENT_LOOP = {"asyncio.get_event_loop", "asyncio.get_running_loop"}


def _scope_nodes(root: ast.AST) -> Iterable[ast.AST]:
    for child in ast.iter_child_nodes(root):
        yield child
        if not isinstance(child, _SCOPES):
            yield from _scope_nodes(child)


def _loads(node: ast.AST) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def _timing_calls(nodes: list[ast.AST]) -> set[ast.Call]:
    """Find calls in arithmetic/comparisons, including clock seeds in local assignments."""
    operands = [node for node in nodes if isinstance(node, (ast.BinOp, ast.Compare))]
    operands.extend(
        node.test for node in nodes if isinstance(node, (ast.Assert, ast.While))
    )
    assignments: dict[str, list[ast.AST]] = {}
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
    pending = {name for operand in operands for name in _loads(operand)}
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        visited.add(name)
        for value in assignments.get(name, ()):
            operands.append(value)
            pending.update(_loads(value) - visited)
    return {
        call
        for operand in operands
        for call in ast.walk(operand)
        if isinstance(call, ast.Call)
    }


def _current_loop_aliases(nodes: list[ast.AST], imports: dict[str, str]) -> set[str]:
    aliases: dict[str, bool] = {}
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            current = (
                isinstance(node.value, ast.Call)
                and dotted(node.value.func, imports) in _CURRENT_LOOP
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = aliases.get(target.id, True) and current
    return {name for name, current in aliases.items() if current}


def _loop_read(
    func: ast.AST, imports: dict[str, str], *, virtual: bool, loop_aliases: set[str]
) -> bool:
    if not isinstance(func, ast.Attribute) or func.attr != "time":
        return False
    receiver = func.value
    if isinstance(receiver, ast.Call):
        name = dotted(receiver.func, imports)
        return name == "asyncio.new_event_loop" or (not virtual and name in _CURRENT_LOOP)
    if virtual and isinstance(receiver, ast.Name) and receiver.id in loop_aliases:
        return False
    name = ast.unparse(receiver).rsplit(".", 1)[-1]
    return name == "loop" or name.endswith("_loop")


def _clock_wait(call: ast.Call, imports: dict[str, str]) -> bool:
    awaited = next((k.value for k in call.keywords if k.arg == "fut"), None)
    if awaited is None and call.args:
        awaited = call.args[0]
    return isinstance(awaited, ast.Call) and (
        dotted(awaited.func, imports) == "asyncio.sleep"
        or isinstance(awaited.func, ast.Attribute)
        and awaited.func.attr == "wait"
    )


def _scope_findings(
    root: ast.AST, inherited: dict[str, str], marked: Set[int], *, virtual: bool = False
) -> Iterable[tuple[int, str]]:
    nodes = list(_scope_nodes(root))
    imports = {**inherited, **import_names(nodes)}
    if isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef)):
        virtual = virtual or is_virtual(root, imports, allow_explicit_install=False)
    # A context manager patches only its body. A bare install may target another
    # loop, or be undone before the call being scanned.
    virtual_body = {
        child
        for node in nodes
        if isinstance(node, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and dotted(item.context_expr.func, imports)
            == "rigplane.testing.timing.VirtualClock.on_running_loop"
            for item in node.items
        )
        for statement in node.body
        for child in ast.walk(statement)
    }
    timing_calls = _timing_calls(nodes)
    loop_aliases = (
        _current_loop_aliases(nodes, imports) if virtual or virtual_body else set()
    )
    for node in nodes:
        on_virtual_loop = virtual or node in virtual_body
        if isinstance(node, _SCOPES):
            yield from _scope_findings(
                node,
                imports,
                marked,
                virtual=on_virtual_loop and isinstance(node, ast.AsyncFunctionDef),
            )
        if not isinstance(node, ast.Call) or node.lineno in marked:
            continue
        name = dotted(node.func, imports)
        primitive = clock_primitive(node, name)
        if primitive == "time.sleep" or (
            primitive == "asyncio.sleep" and not on_virtual_loop
        ):
            yield node.lineno, primitive
        elif node in timing_calls and (
            primitive == "clock-read"
            or _loop_read(
                node.func, imports, virtual=on_virtual_loop, loop_aliases=loop_aliases
            )
        ):
            yield node.lineno, "wall-clock measurement"
        elif (
            not on_virtual_loop
            and name == "asyncio.wait"
            and timeout_value(node, None) is not None
        ):
            yield node.lineno, "asyncio.wait with literal timeout"
        elif (
            not on_virtual_loop
            and name == "asyncio.wait_for"
            and timeout_value(node, 1) is not None
            and _clock_wait(node, imports)
        ):
            yield node.lineno, "asyncio.wait_for with literal timeout"


def analyze_python(source: str) -> tuple[list[tuple[int, str]], dict[int, str]]:
    """Return per-call violations and exemption comments from a Python test file."""
    tree = ast.parse(source)
    markers = realtime_markers(source)
    return sorted(_scope_findings(tree, {}, markers.keys())), markers


def scan_python(source: str) -> list[tuple[int, str]]:
    """Return per-call violations in a Python test file, including its helpers."""
    return analyze_python(source)[0]
