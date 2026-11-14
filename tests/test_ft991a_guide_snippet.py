"""Guide snippet check for the FT-991A USB setup page.

Reads the ```python blocks of docs/guide/ft991a-usb-setup.md, parses them
with ast, and checks that every name imported from rigplane exists and that
asyncio.run() receives a call (main()) rather than a bare name.
"""

import ast
import importlib
from pathlib import Path

GUIDE = (
    Path(__file__).resolve().parent.parent / "docs" / "guide" / "ft991a-usb-setup.md"
)


def _python_blocks(text):
    blocks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("```python"):
            i += 1
            chunk = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                chunk.append(lines[i])
                i += 1
            blocks.append("\n".join(chunk))
        i += 1
    return blocks


def test_ft991a_guide_snippets():
    text = GUIDE.read_text(encoding="utf-8")
    blocks = _python_blocks(text)
    assert blocks, f"no ```python blocks found in {GUIDE}"
    package = importlib.import_module("rigplane")
    run_calls = 0
    for block in blocks:
        tree = ast.parse(block)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "rigplane":
                for name in node.names:
                    assert hasattr(package, name.name), (
                        f"rigplane has no {name.name!r} imported by {GUIDE.name}"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                run_calls += 1
                assert node.args and isinstance(node.args[0], ast.Call), (
                    "asyncio.run(...) in the guide must take a call, "
                    "e.g. asyncio.run(main())"
                )
    assert run_calls, f"no asyncio.run(...) call found in {GUIDE}"
