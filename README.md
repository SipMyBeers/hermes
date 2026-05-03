# Hermes

Email triage + invoicing agent for ghengis. See `docs/specs/` for design,
`docs/plans/` for implementation.

## Quick start (after install on ghengis)

    hermes-invoice-cli --client boarhog --month 2026-05 --send
    hermes-mail --dry-run --since 24h
    hermes-tg            # foreground bot
    hermes-digest        # one-shot morning report
