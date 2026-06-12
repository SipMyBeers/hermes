"""Phase 2 lead sources: ignore-domains, contact forms, partner forwards, LLM confirm."""
from email.message import EmailMessage
from hermes.config import ClientConfig
from hermes.extract.lead import (
    detect_lead, detect_form_lead, detect_partner_lead,
    extract_forward_original, confirm_lead_llm,
)


def _msg(from_addr, subject="Need a website", body="Saw your work, want a quote",
         to="dzbeers747@gmail.com", name=""):
    m = EmailMessage()
    m["From"] = f"{name} <{from_addr}>" if name else from_addr
    m["To"] = to
    m["Subject"] = subject
    m.set_content(body)
    return m


def test_ignore_domain_never_a_lead():
    sig = detect_lead(
        _msg("recruiter@kumquat.life"), known_client_domains=set(),
        ignore_domains={"kumquat.life"},
    )
    assert sig.is_lead is False
    assert "ignore" in sig.reason


def test_form_lead_via_to_address():
    m = _msg("formmailer@some-site.com", to="leads@ranger-beers.com")
    assert detect_form_lead(m, "leads@ranger-beers.com") is True


def test_form_lead_via_delivered_to():
    m = _msg("formmailer@some-site.com")
    m["Delivered-To"] = "LEADS@ranger-beers.com"
    assert detect_form_lead(m, "leads@ranger-beers.com") is True


def test_form_lead_negative():
    assert detect_form_lead(_msg("x@y.com"), "leads@ranger-beers.com") is False
    assert detect_form_lead(_msg("x@y.com", to="leads@ranger-beers.com"), "") is False


def test_extract_forward_original():
    body = (
        "rainbowlasers.com\n---------- Forwarded message ---------\n"
        "From: New Prospect <prospect@dentist-office.com>\n"
        "Date: Sat, Jun 6, 2026\nSubject: Website inquiry\n\nHi, I need a site."
    )
    m = _msg("magic@rainbowlasers.com", subject="Fwd: Website inquiry", body=body)
    got = extract_forward_original(m)
    assert got == ("New Prospect", "prospect@dentist-office.com")


def test_partner_lead_detected_for_unknown_forward():
    clients = {"tbn": ClientConfig(name="tbn", contact_email="todd@tbn.net",
                                   active=True, domain="tbn.net")}
    body = (
        "---------- Forwarded message ---------\n"
        "From: New Prospect <prospect@dentist-office.com>\n"
        "Subject: Website inquiry\n"
    )
    m = _msg("magic@rainbowlasers.com", subject="Fwd: Website inquiry", body=body)
    sig = detect_partner_lead(
        m, clients, partner_addrs={"magic@rainbowlasers.com"}, ignore_domains=set(),
    )
    assert sig.is_lead is True
    assert "prospect@dentist-office.com" in sig.sender
    assert "partner" in sig.reason


def test_partner_lead_not_fired_for_known_client_forward():
    clients = {"maxcrofton": ClientConfig(name="maxcrofton",
                                          contact_email="maxcrofton@gmail.com", active=True)}
    body = (
        "---------- Forwarded message ---------\n"
        "From: Max Crofton <maxcrofton@gmail.com>\nSubject: Re: site\n"
    )
    m = _msg("magic@rainbowlasers.com", subject="Fwd: Re: site", body=body)
    sig = detect_partner_lead(
        m, clients, partner_addrs={"magic@rainbowlasers.com"}, ignore_domains=set(),
    )
    assert sig.is_lead is False


def test_partner_lead_respects_ignore_domains():
    body = (
        "---------- Forwarded message ---------\n"
        "From: Recruiter <r@kumquat.life>\nSubject: dialers\n"
    )
    m = _msg("magic@rainbowlasers.com", body=body)
    sig = detect_partner_lead(m, {}, partner_addrs={"magic@rainbowlasers.com"},
                              ignore_domains={"kumquat.life"})
    assert sig.is_lead is False


def test_confirm_lead_llm_yes(monkeypatch):
    import hermes.extract.lead as lead_mod

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": '{"is_lead": true, "why": "asks for site"}'}

    monkeypatch.setattr(lead_mod.httpx, "post", lambda *a, **k: FakeResp())
    assert confirm_lead_llm(_msg("p@x.com")) is True


def test_confirm_lead_llm_no(monkeypatch):
    import hermes.extract.lead as lead_mod

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": '{"is_lead": false, "why": "newsletter"}'}

    monkeypatch.setattr(lead_mod.httpx, "post", lambda *a, **k: FakeResp())
    assert confirm_lead_llm(_msg("p@x.com")) is False


def test_confirm_lead_llm_defaults_true_on_failure(monkeypatch):
    import hermes.extract.lead as lead_mod

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(lead_mod.httpx, "post", boom)
    assert confirm_lead_llm(_msg("p@x.com")) is True
