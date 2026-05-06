"""Smoke test: every public module imports cleanly without side effects.

Catches the class of bug where a test file is green but a console script
fails at startup because the module's top-level code raises (e.g. circular
import, missing optional dep, NotImplementedError on import)."""
import importlib
import pytest


_MODULES = [
    "hermes.config",
    "hermes.db",
    "hermes.actions.store",
    "hermes.actions.job",
    "hermes.bookmarks.store",
    "hermes.bookmarks.job",
    "hermes.classifier.rules",
    "hermes.classifier.llm",
    "hermes.classifier.drafter",
    "hermes.classifier.learned",
    "hermes.digest.build",
    "hermes.digest.enrich",
    "hermes.digest.job",
    "hermes.digest.weekly",
    "hermes.digest.weekly_job",
    "hermes.extract.lead",
    "hermes.extract.receipt",
    "hermes.extract.ledger_writer",
    "hermes.ingest.frames",
    "hermes.ingest.ocr",
    "hermes.ingest.summary",
    "hermes.ingest.url",
    "hermes.ingest.video",
    "hermes.ingest.web",
    "hermes.ingest.webclaw",
    "hermes.invoice.cli",
    "hermes.invoice.job",
    "hermes.invoice.render",
    "hermes.mail.daemon",
    "hermes.mail.imap_client",
    "hermes.mail.smtp_client",
    "hermes.rounds.job",
    "hermes.rss.poller",
    "hermes.stale.check",
    "hermes.stale.job",
    "hermes.telegram.client",
    "hermes.telegram.commands",
    "hermes.telegram.daemon",
    "hermes.telegram.keyboards",
    "hermes.voice.transcribe",
]


@pytest.mark.parametrize("modname", _MODULES)
def test_module_imports(modname: str):
    """Module must import cleanly with no side effects that hit external systems."""
    importlib.import_module(modname)
