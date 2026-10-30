"""The calendar (B23, octo/events.py): its links and this Mac's calendars (Mac only like the rest of /settings), and the
Octo app's post of them; and the month and day views of conversations (B37, octo/month.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .common import Lab, same_origin


class CalendarLink(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class CalendarMac(BaseModel):
    on: bool


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    # From above (B37, octo/month.py): a month of conversations, then a day. Read only, the phone too.
    @router.get("/calendar/month")
    def calendar_month(month: str | None = None) -> dict:
        from .. import month as MO
        try:
            return MO.month(db(), month)
        except MO.MonthError as e:
            raise HTTPException(422, str(e))

    @router.get("/calendar/day")
    def calendar_day(date: str | None = None) -> dict:
        from .. import month as MO
        try:
            return MO.day(db(), date)
        except MO.MonthError as e:
            raise HTTPException(422, str(e))

    # The calendar (B23, Mac only like the rest of /settings): links go in, never out; the page sees host and last four.
    @router.get("/settings/calendar")
    def calendar_settings() -> dict:
        from .. import events as EV
        return EV.settings()

    @router.post("/settings/calendar/links", dependencies=[Depends(same_origin)])
    def calendar_add(body: CalendarLink) -> dict:
        from .. import events as EV
        try:
            return EV.add_link(db(), body.url)
        except EV.CalendarError as e:
            raise HTTPException(400, str(e))

    @router.post("/settings/calendar/links/{link_id}/remove", dependencies=[Depends(same_origin)])
    def calendar_remove(link_id: str) -> dict:
        from .. import events as EV
        return EV.remove_link(db(), link_id)

    @router.post("/settings/calendar/mac", dependencies=[Depends(same_origin)])
    def calendar_mac(body: CalendarMac) -> dict:
        from .. import events as EV
        return EV.set_mac(db(), body.on)

    @router.post("/mac-app/calendar", dependencies=[Depends(same_origin)])
    async def mac_app_calendar(request: Request) -> dict:
        """The Octo app's read of this Mac's calendars (EventKit): {"access": "full"|"denied"|"ask", "events"}."""
        from .. import events as EV
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "an object")
        return EV.from_mac(db(), body)
