import sys


USAGE = """things-tui [quick] [--terminal | --night] [--help]

  quick        quick-add popup (one line, enter closes)
  --terminal   transparent: no backgrounds, uses your terminal's colors
  --night      the built-in dark palette (default)
Preference persists in ~/.config/things-tui/config.toml (T toggles in-app);
THINGS_TUI_THEME=terminal|night overrides it.
"""


def main() -> None:
    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return
    theme = None
    if "--terminal" in args or "--transparent" in args:
        theme = "terminal"
    elif "--night" in args:
        theme = "night"
    if args and args[0] in ("quick", "--quick", "-q"):
        from .quick import run
        run(theme)
    else:
        from .app import run
        run(theme)


if __name__ == "__main__":
    main()
