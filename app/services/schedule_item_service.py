"""Validation, serialization, and organizer writes for non-match schedule items.

Shared by schedule editing (the SPA routes and the public API) and imports.
Callers check organizer permission.
"""

from datetime import datetime, timezone, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.models.constants import LONG_NAME_LEN, SHORT_NAME_LEN
from models import Field, ScheduleItem, SideComp, db


def _text(value, label, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label} must be {limit} characters or fewer.")
    return value or None


def _time(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} is required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(f"{label} must include a timezone.")
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        raise ValueError(f"{label} must include a valid date, time, and timezone.") from None


def validate_schedule_item(event, data, *, field_names=None, linked=False):
    """Return normalized values or raise ValueError before any writes occur.

    ``linked`` validates timing for a side competition being created in the same
    transaction. Imports may supply the field names they are about to create.
    """
    if not isinstance(data, dict):
        raise ValueError("Schedule details must be an object.")
    start = _time(data.get("start_time"), "Start time")
    end = _time(data.get("end_time"), "End time")
    if end <= start:
        raise ValueError("End time must be after start time.")
    if end - start > timedelta(days=31):
        raise ValueError("An activity can last up to 31 days. Split longer activities into separate entries.")
    request_id = data.get("request_id")
    if request_id is not None:
        try:
            request_id = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("The request identifier is invalid.") from None
    field = _text(data.get("field"), "Field", SHORT_NAME_LEN)
    if field_names is None:
        field_names = {f.name for f in Field.query.filter_by(event=event).all()}
    if field and field not in field_names:
        raise ValueError("Choose a field from this event.")
    sidecomp_id = data.get("sidecomp_id")
    if sidecomp_id is not None:
        if type(sidecomp_id) is not int or not SideComp.query.filter_by(id=sidecomp_id, event=event).first():
            raise ValueError("Choose a side competition from this event.")
    is_linked = linked or sidecomp_id is not None
    title = None if is_linked else _text(data.get("title"), "Name", LONG_NAME_LEN)
    if not is_linked and not title:
        raise ValueError("Name is required.")
    return {
        "request_id": request_id,
        "start_time": start,
        "end_time": end,
        "field": field,
        "location": _text(data.get("location"), "Location", LONG_NAME_LEN),
        "sidecomp_id": sidecomp_id,
        "title": title,
        "description": None if is_linked else _text(data.get("description"), "Description", 20000),
    }


def schedule_item_payload(item):
    """Resolve linked content on every read so the schedule stays current."""
    comp = item.sidecomp
    return {
        "id": item.id,
        "sidecomp_id": item.sidecomp_id,
        "title": comp.name if comp else item.title,
        "description": comp.description if comp else item.description,
        "start_time": item.start_time.isoformat() + "Z",
        "end_time": item.end_time.isoformat() + "Z",
        "field": item.field,
        "location": item.location,
    }


def schedule_items_payload(event):
    return [
        schedule_item_payload(item)
        for item in ScheduleItem.query.filter_by(event=event).order_by(ScheduleItem.start_time, ScheduleItem.id).all()
    ]


def schedule_item_matches(item, values, *, new_sidecomp=False):
    """Only replay a saved creation when the caller is retrying the same draft."""
    keys = ("title", "description", "start_time", "end_time", "field", "location")
    return (
        item.created_sidecomp == new_sidecomp
        and all(getattr(item, key) == values[key] for key in keys)
        and (new_sidecomp or item.sidecomp_id == values["sidecomp_id"])
    )


def _creation_replay(item, values) -> Result[tuple, ArctosError]:
    if not schedule_item_matches(item, values):
        return Err(
            ArctosError(
                "This activity was already saved. Close this form and edit it from the schedule.", status_code=409
            )
        )
    return Ok((item, False))


def create_schedule_item(event, data) -> Result[tuple, ArctosError]:
    """Create an item from the JSON *data*; ``Ok((item, created))``.

    A retry with an already-used ``request_id`` returns the saved item with
    ``created`` false when its details are unchanged, and a 409 otherwise.
    """
    try:
        values = validate_schedule_item(event, data)
    except ValueError as error:
        return Err(ValidationError(str(error)))
    request_id = values["request_id"]
    existing = ScheduleItem.query.filter_by(event=event, request_id=request_id).first() if request_id else None
    if existing:
        return _creation_replay(existing, values)
    item = ScheduleItem(event=event, **values)
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = ScheduleItem.query.filter_by(event=event, request_id=request_id).first() if request_id else None
        if existing:
            return _creation_replay(existing, values)
        raise
    return Ok((item, True))


def update_schedule_item(event, item_id, data) -> Result[ScheduleItem, ArctosError]:
    """Replace an item's details with the JSON *data*."""
    item = ScheduleItem.query.filter_by(id=item_id, event=event).first()
    if item is None:
        return Err(NotFoundError("Schedule item not found"))
    try:
        values = validate_schedule_item(event, data)
    except ValueError as error:
        return Err(ValidationError(str(error)))
    values.pop("request_id", None)
    for key, value in values.items():
        setattr(item, key, value)
    db.session.commit()
    db.session.expire(item)
    return Ok(item)


def delete_schedule_item(event, item_id) -> Result[None, ArctosError]:
    item = ScheduleItem.query.filter_by(id=item_id, event=event).first()
    if item is None:
        return Err(NotFoundError("Schedule item not found"))
    db.session.delete(item)
    db.session.commit()
    return Ok(None)
