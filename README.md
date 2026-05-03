# Hermes

Email triage + invoicing agent for ghengis. See `docs/specs/` for design,
`docs/plans/` for implementation.

## Quick start (after install on ghengis)

    hermes-invoice-cli --client boarhog --month 2026-05 --send
    hermes-mail --dry-run --since 24h
    hermes-tg            # foreground bot
    hermes-digest        # one-shot morning report

## Dev

    uv sync
    uv run pytest

On macOS, WeasyPrint's PDF render needs Homebrew's pango/cairo/glib in the
library search path. Install once:

    brew install pango cairo gdk-pixbuf glib libffi

Then run pytest with:

    DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest

Without the env var, the three PDF-touching tests skip with a reason
mentioning ghengis (where the path is wired in the launchd plist).
