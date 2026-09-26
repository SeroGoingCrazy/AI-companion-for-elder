from __future__ import annotations

from sqlalchemy.orm import Session

from elder_companion.models import DEFAULT_ELDER_ID, Elder


class ElderNotFound(LookupError):
    def __init__(self, elder_id: int) -> None:
        super().__init__(f"elder {elder_id} not found")
        self.elder_id = elder_id


def get_elder(session: Session, elder_id: int | None) -> Elder:
    """Look up an elder, defaulting to the demo elder; raise ElderNotFound if missing."""
    elder_id = elder_id or DEFAULT_ELDER_ID
    elder = session.get(Elder, elder_id)
    if elder is None:
        raise ElderNotFound(elder_id)
    return elder
