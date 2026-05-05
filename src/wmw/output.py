"""Rich console output helpers for wmw."""

from __future__ import annotations

import builtins
import os
import sys
from typing import Any, TextIO

try:
    from rich.console import Console
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
    _RICH = True
except ImportError:
    Console = None  # type: ignore[misc,assignment]
    Rule = None  # type: ignore[misc,assignment]
    Table = None  # type: ignore[misc,assignment]
    Text = None  # type: ignore[misc,assignment]
    Theme = None  # type: ignore[misc,assignment]
    _RICH = False


WMW_THEME = (
    Theme(
        {
            "wmw.error":   "bold #e85d75",
            "wmw.warning": "bold #d6a642",
            "wmw.info":    "bold #5f9ea0",
            "wmw.success": "bold #7fb069",
            "wmw.heading": "bold #7fb069",
            "wmw.rule":    "bold #5f9ea0",
            "wmw.prompt":  "bold #5f9ea0",
            "wmw.muted":   "#b7c7d3",
            "wmw.text":    "#e6edf3",
        }
    )
    if _RICH
    else None
)


def _color_disabled() -> bool:
    return bool(os.environ.get("WMW_NO_COLOR"))


def _should_force_terminal(stream: TextIO | None) -> bool:
    if _color_disabled():
        return False
    return stream in {sys.__stdout__, sys.__stderr__}


def _console_kwargs(stream: TextIO | None) -> dict[str, Any]:
    force_terminal = _should_force_terminal(stream)
    kwargs: dict[str, Any] = {
        "highlight": False,
        "soft_wrap": True,
        "theme": WMW_THEME,
        "force_terminal": force_terminal,
    }
    if force_terminal:
        kwargs["color_system"] = "truecolor"
        kwargs["no_color"] = False
    return kwargs


def _target_console(file: TextIO | None):
    if not _RICH:
        return None
    stream = file or sys.stdout
    return Console(file=stream, **_console_kwargs(stream))


def print(
    *objects: Any,
    sep: str = " ",
    end: str = "\n",
    file: TextIO | None = None,
    flush: bool = False,
    style: str | None = None,
) -> None:
    target = _target_console(file)
    if target is None:
        builtins.print(*objects, sep=sep, end=end, file=file, flush=flush)
        return
    text = sep.join(str(obj) for obj in objects)
    if style and Text is not None:
        rendered = Text(text, style=style)
    elif Text is not None:
        rendered = Text(text)
    else:
        rendered = text  # type: ignore[assignment]
    target.print(rendered, end=end, markup=False, highlight=False)
    if flush:
        target.file.flush()


def info(msg: str) -> None:
    print(f"INFO: {msg}", style="wmw.info")


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", style="wmw.warning")


def error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, style="wmw.error")


def success(msg: str) -> None:
    print(f"OK: {msg}", style="wmw.success")


def section(title: str) -> None:
    target = _target_console(None)
    if target is None or Rule is None:
        builtins.print(f"\n{title}\n{'=' * len(title)}")
        return
    target.print()
    target.print(
        Rule(Text(title, style="wmw.heading"), characters="=", style="wmw.rule")
    )


def make_table(*columns: str) -> Any:
    """Return a Rich Table pre-configured with wmw style, or None if Rich is unavailable."""
    if not _RICH or Table is None:
        return None
    tbl = Table(show_header=True, header_style="wmw.heading", border_style="wmw.rule")
    for col in columns:
        tbl.add_column(col)
    return tbl


def render_table(table: Any) -> None:
    if table is None:
        return
    target = _target_console(None)
    if target is not None:
        target.print(table)
