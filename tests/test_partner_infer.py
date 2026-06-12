"""infer_client: which client does a partner (Josh) thread belong to?"""
from email.message import EmailMessage
from hermes.classifier.partner import infer_client
from hermes.config import ClientConfig


def _clients():
    return {
        "tbn": ClientConfig(
            name="tbn", contact_email="todd@tbn.net", active=True, domain="tbn.net",
        ),
        "maxcrofton": ClientConfig(
            name="maxcrofton", contact_email="maxcrofton@gmail.com", active=True,
            bill_to_company="Max Crofton",
        ),
    }


def _msg(to="", cc="", subject="hello", body="body text"):
    m = EmailMessage()
    m["From"] = "Rainbow Lasers <magic@rainbowlasers.com>"
    if to:
        m["To"] = to
    if cc:
        m["Cc"] = cc
    m["Subject"] = subject
    m.set_content(body)
    return m


def test_participant_contact_match():
    msg = _msg(to="todd@tbn.net", cc="dzbeers747@gmail.com", subject="Re: Quote #1052221")
    assert infer_client(msg, _clients()) == "tbn"


def test_participant_domain_match():
    msg = _msg(to="someone-else@tbn.net", subject="assets")
    assert infer_client(msg, _clients()) == "tbn"


def test_forwarded_block_contact_match():
    body = (
        "rainbowlasers.com\n---------- Forwarded message ---------\n"
        "From: Max Crofton <maxcrofton@gmail.com>\nDate: Sat, Jun 6\n"
        "Subject: Max Crofton Personal Website\n"
    )
    msg = _msg(to="dzbeers747@gmail.com", subject="Fwd: Personal Website", body=body)
    assert infer_client(msg, _clients()) == "maxcrofton"


def test_subject_key_token_match():
    msg = _msg(to="dzbeers747@gmail.com", subject="Re: Quote #1052221 / TBN")
    assert infer_client(msg, _clients()) == "tbn"


def test_subject_company_match():
    msg = _msg(to="dzbeers747@gmail.com", subject="Max Crofton site feedback")
    assert infer_client(msg, _clients()) == "maxcrofton"


def test_no_match_returns_none():
    msg = _msg(to="dzbeers747@gmail.com", subject="family bbq sunday")
    assert infer_client(msg, _clients()) is None


def test_subject_token_requires_word_boundary():
    """'tbn' inside another word must not match."""
    msg = _msg(to="dzbeers747@gmail.com", subject="catbnip toys")
    assert infer_client(msg, _clients()) is None
