"""Public unsubscribe endpoint for campaign recipients.

The URL is mailed in ``List-Unsubscribe`` headers and in the rendered footer, so
it must work without a session. The token is HMAC-signed and carries the
organization, address and campaign, which is the only authorization needed:
possessing the link is proof the sender mailed that address.

``GET`` renders a one-line confirmation page. ``POST`` answers 204 for RFC 8058
one-click unsubscribe, which mail clients send without showing a page. Both are
idempotent.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse
from loguru import logger

from api.db import db_client
from api.enums import EmailSuppressionReason
from api.services.email.unsubscribe import (
    UnsubscribeTokenError,
    parse_unsubscribe_token,
)

router = APIRouter(prefix="/unsubscribe")

_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Unsubscribed</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 32rem; margin: 4rem auto;">
<h1 style="font-size: 1.25rem;">You are unsubscribed</h1>
<p>We will not send further marketing email to this address.</p>
</body>
</html>
"""


def _claim(token: str):
    try:
        return parse_unsubscribe_token(token)
    except UnsubscribeTokenError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _suppress(token: str) -> bool:
    claim = _claim(token)
    created = await db_client.suppress_address(
        claim.organization_id,
        claim.email,
        reason=EmailSuppressionReason.UNSUBSCRIBED.value,
        campaign_id=claim.campaign_id,
    )
    logger.info(
        "Unsubscribe recorded for organization {} campaign {} (new={})",
        claim.organization_id,
        claim.campaign_id,
        created,
    )
    return created


@router.get("/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str) -> HTMLResponse:
    await _suppress(token)
    return HTMLResponse(_PAGE)


@router.post("/{token}", status_code=204)
async def unsubscribe_one_click(token: str) -> Response:
    await _suppress(token)
    return Response(status_code=204)
