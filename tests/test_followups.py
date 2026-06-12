"""Follow-up message + voicemail options, personalized per pipeline contact."""
from hermes.pipeline.followups import render_email_options, render_voicemail_options


def test_email_options_personalized_and_pasteable():
    opts = render_email_options("Pat", project_hint="your website")
    assert len(opts) == 3
    for title, subject, body in opts:
        assert title
        assert subject
        assert "Pat" in body
        # No-brackets rule: output must be literally pasteable.
        assert "[" not in body and "{" not in body
        assert "[" not in subject
        assert body.rstrip().endswith("Dylan Beers")


def test_email_options_distinct_angles():
    titles = [t for t, _, _ in render_email_options("Pat")]
    assert len(set(titles)) == 3


def test_voicemail_options_personalized_and_short():
    opts = render_voicemail_options("Pat")
    assert len(opts) == 3
    for title, script in opts:
        assert "Pat" in script
        assert "[" not in script and "{" not in script
        # A voicemail should read in ~20-30 seconds.
        assert len(script.split()) <= 90


def test_default_project_hint():
    opts = render_email_options("Pat")
    assert any("site" in body.lower() or "website" in body.lower()
               for _, _, body in opts)
