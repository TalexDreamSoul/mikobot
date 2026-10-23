"""Mikobot ships as `nanobot-ai`, so upstream's release channel must never update it."""

UPSTREAM_UPDATES_ENABLED = False

UPDATES_UNAVAILABLE_MESSAGE = (
    "In-app updates are unavailable for Mikobot. "
    "Deploy a new Mikobot build instead of installing from the upstream nanobot-ai release channel."
)
