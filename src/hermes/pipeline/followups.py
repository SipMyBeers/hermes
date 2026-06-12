"""Personalized follow-up messages + voicemail scripts for pipeline contacts.

Rendered with the contact's real first name so every option is literally
copy-pasteable (or readable aloud) as-is — no brackets, no placeholders.
Three angles each, ordered by escalation: light nudge, value-add, breakup.
"""
from __future__ import annotations


def render_email_options(
    first_name: str,
    project_hint: str = "your site",
) -> list[tuple[str, str, str]]:
    """[(title, subject, body)] — three follow-up emails, escalating."""
    return [
        (
            "Light nudge",
            "Quick follow-up",
            f"Hey {first_name},\n\n"
            f"Just floating this back to the top of your inbox. Still happy to "
            f"help with {project_hint} whenever you're ready — no rush on my end.\n\n"
            f"— Dylan Beers",
        ),
        (
            "Value-add",
            f"An idea for {project_hint}",
            f"Hey {first_name},\n\n"
            f"Was thinking about {project_hint} this week and sketched out how "
            f"I'd approach the first version: clean single-page build, fast on "
            f"phones, easy for you to update yourself after handoff. Happy to "
            f"put a live preview together so you can react to something real "
            f"instead of a proposal doc.\n\n"
            f"Want me to spin that up?\n\n"
            f"— Dylan Beers",
        ),
        (
            "Breakup",
            "Should I close this out?",
            f"Hey {first_name},\n\n"
            f"I know things get busy, so this is my last check-in. If the "
            f"timing isn't right for {project_hint}, no hard feelings — I'll "
            f"close the file and you can always reach back out down the road.\n\n"
            f"If you do still want it, just reply with a good time to talk and "
            f"I'll take it from there.\n\n"
            f"— Dylan Beers",
        ),
    ]


def render_voicemail_options(first_name: str) -> list[tuple[str, str]]:
    """[(title, script)] — three ~20-second voicemails, read-aloud ready.

    No phone number baked in: caller ID carries it, and each script tells
    them to use this number back.
    """
    return [
        (
            "Light nudge",
            f"Hey {first_name}, it's Dylan Beers. Just following up on the "
            f"website — wanted to put a voice to the emails. No rush at all, "
            f"but if you've got questions I can answer them in five minutes "
            f"flat. Call or text me back at this number whenever works. "
            f"Talk soon.",
        ),
        (
            "Momentum",
            f"Hey {first_name}, Dylan Beers here. I've already got some ideas "
            f"laid out for your site and I'd love to show you something real "
            f"rather than describe it — I can have a live preview link in "
            f"front of you this week. Call or text me back at this number and "
            f"we'll make it happen. Thanks!",
        ),
        (
            "Last call",
            f"Hey {first_name}, it's Dylan Beers. I've reached out a couple "
            f"times about the website, so I'll keep this short — if it's not "
            f"the right time, totally fine, this is my last call about it. If "
            f"you do want it done, text me back at this number and consider it "
            f"handled. Either way, all the best.",
        ),
    ]
