"""
Shared Slack client: one retrying caller, plus file download/upload.

Kept separate from post_digest.py so both the digest and the PDF mirror share
exactly one place where retries, rate limits and error handling are decided.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://slack.com/api/"

RETRYABLE = {"ratelimited", "service_unavailable", "internal_error"}


class SlackError(RuntimeError):
    pass


def token() -> str:
    tok = os.environ.get("SLACK_BOT_TOKEN")
    if not tok:
        raise RuntimeError("SLACK_BOT_TOKEN is not set.")
    return tok


def call(method: str, payload: dict = None, get: bool = False) -> dict:
    """Call a Slack Web API method, retrying only what is worth retrying.

    A bad token or a missing scope will never succeed on retry, so those raise
    immediately with Slack's own error string — which is usually the exact name
    of the scope you forgot to add.
    """
    payload = payload or {}
    last = None
    for attempt in range(4):
        try:
            if get:
                url = BASE + method + "?" + urllib.parse.urlencode(payload)
                req = urllib.request.Request(
                    url, headers={"Authorization": f"Bearer {token()}"}
                )
            else:
                req = urllib.request.Request(
                    BASE + method,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {token()}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                )
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read())
            if body.get("ok"):
                return body
            err = body.get("error", "unknown_error")
            if err in RETRYABLE:
                last = err
                time.sleep(2 ** attempt)
                continue
            raise SlackError(f"{method} failed: {err}")
        except urllib.error.URLError as e:
            last = str(e)
            time.sleep(2 ** attempt)
    raise SlackError(f"{method} unreachable after retries: {last}")


def download_file(url_private_download: str) -> bytes:
    """Fetch a Slack-hosted file. Needs the bot token — these URLs are not public."""
    req = urllib.request.Request(
        url_private_download, headers={"Authorization": f"Bearer {token()}"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def upload_file(channel: str, filename: str, data: bytes,
                title: str = None, initial_comment: str = None,
                thread_ts: str = None) -> dict:
    """Upload bytes to a channel or DM using Slack's three-step external flow.

    files.upload was retired; the replacement is: ask for a URL, PUT the bytes
    there, then tell Slack to attach the finished file to a conversation.
    """
    up = call("files.getUploadURLExternal",
              {"filename": filename, "length": len(data)}, get=True)

    put = urllib.request.Request(up["upload_url"], data=data, method="POST")
    with urllib.request.urlopen(put, timeout=120) as resp:
        resp.read()

    payload = {
        "files": json.dumps([{"id": up["file_id"], "title": title or filename}]),
        "channel_id": channel,
    }
    if initial_comment:
        payload["initial_comment"] = initial_comment
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return call("files.completeUploadExternal", payload, get=True)


def open_dm(user_id: str) -> str:
    """Return the DM channel ID for a user (idempotent — safe to call daily)."""
    return call("conversations.open", {"users": user_id})["channel"]["id"]


def permalink(channel: str, ts: str) -> str:
    return call("chat.getPermalink", {"channel": channel, "message_ts": ts},
                get=True)["permalink"]


def history(channel: str, oldest: str = None, limit: int = 200) -> list:
    p = {"channel": channel, "limit": limit}
    if oldest:
        p["oldest"] = oldest
    return call("conversations.history", p, get=True).get("messages", [])


def replies(channel: str, ts: str) -> list:
    return call("conversations.replies",
                {"channel": channel, "ts": ts, "limit": 200},
                get=True).get("messages", [])


def add_reaction(channel: str, ts: str, name: str) -> bool:
    """Returns False if the reaction was already there — which is how the mirror
    knows a reply has already been forwarded, with no database involved."""
    try:
        call("reactions.add", {"channel": channel, "timestamp": ts, "name": name})
        return True
    except SlackError as e:
        if "already_reacted" in str(e):
            return False
        raise
