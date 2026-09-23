"""
SteelSpec review UI — milestone 7AN.

The smallest genuine vertical slice of the review UI: server-rendered pages
that consume the existing 7AM view model as their single render contract and
the existing 7AJ workflow as the only way any state changes.

    workflow (7AJ) -> contract (7AK) -> view model (7AM) -> pages (render.py)
             ^                                                  |
             +-------- resolution / refresh (session.py) <------+

Nothing in this package computes decisions, counts, statuses or engineering
values; nothing invents actions; failure states are displayed exactly as the
view model states them.
"""

from app.review_ui import render, session, web

__all__ = ["render", "session", "web"]
