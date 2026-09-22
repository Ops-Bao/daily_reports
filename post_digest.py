"""
Slack delivery layer.

One entry point: post(destination, text). A destination is either a channel ID
(C…) or a user ID (U…) — chat.postMessage accepts both, so DMs and channels take
the same path with no branching. Long digests are split on section boundaries
because Slack silently truncates a message over ~40k characters, and a truncated
ops digest is worse than two messages.
"""

import time

import slack_api

# Slack's hard limit is 40000 chars; stay well under so we never test the edge.
MAX_CHARS = 30000


def _split(text: str, sep: str = "\n\n———\n\n") -> list:
    """Split into Slack-sized chunks on section boundaries, never mid-sentence."""
    if len(text) <= MAX_CHARS:
        return [text]
    chunks, current = [], ""
    for block in text.split(sep):
        candidate = block if not current else current + sep + block
        if len(candidate) > MAX_CHARS and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    # A single section can still be too big on its own — one manager pasting a
    # very long GENERAL note is enough. Section splitting can't help there, so
    # cut on length as a last resort rather than letting Slack truncate it.
    out = []
    for c in chunks:
        while len(c) > MAX_CHARS:
            cut = c.rfind("\n", 0, MAX_CHARS)
            if cut <= 0:
                cut = MAX_CHARS
            out.append(c[:cut])
            c = c[cut:].lstrip("\n")
        if c:
            out.append(c)
    return out


def _channel_for(destination: str) -> str:
    """chat.postMessage accepts a user ID, conversations.history does not."""
    return slack_api.open_dm(destination) if destination.startswith("U") else destination


def contains_header(messages: list, header: str) -> bool:
    return any(header in (m.get("text") or "") for m in messages)


def already_posted(destination: str, header: str, hours: int = 36) -> bool:
    """True if a message carrying `header` is already in the destination.

    This is what makes the morning run idempotent. GitHub can start a scheduled
    job hours late and both DST crons fire every day, so the job cannot know
    from the clock whether it is the first one. Slack knows: if today's header
    is already there, this run has nothing to add. Needs groups:history /
    channels:history for the ops channel and im:history for the DM.
    """
    oldest = time.time() - hours * 3600
    msgs = slack_api.history(_channel_for(destination), oldest=f"{oldest:.6f}")
    return contains_header(msgs, header)


def post(destination: str, text: str, dry_run: bool = False) -> list:
    """Post text to a channel ID or user ID. Returns the message timestamps."""
    if dry_run:
        print(f"\n===== DRY RUN → {destination} =====\n{text}\n")
        return []
    tss = []
    for i, chunk in enumerate(_split(text)):
        body = slack_api.call("chat.postMessage", {
            "channel": destination,
            "text": chunk,
            "unfurl_links": False,
            "unfurl_media": False,
        })
        tss.append(body["ts"])
        if i:
            time.sleep(1)  # stay inside chat.postMessage's 1/sec per-channel tier
    return tss
