import logging
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

import config

GIST_API_URL = f"https://api.github.com/gists/{config.GIST_ID}"

HEADERS = {
    "Authorization": f"Bearer {config.GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


class VerificationStats(BaseModel):
    total: int
    last_verified: str | None


async def fetch_stats() -> VerificationStats:
    async with httpx.AsyncClient() as client:
        response = await client.get(GIST_API_URL, headers=HEADERS)
        response.raise_for_status()
        content = response.json()["files"]["verifications.json"]["content"]
        return VerificationStats.model_validate_json(content)


async def increment_verification() -> None:
    try:
        stats = await fetch_stats()
        updated = VerificationStats(
            total=stats.total + 1, last_verified=datetime.now(timezone.utc).isoformat()
        )
        async with httpx.AsyncClient() as client:
            await client.patch(
                GIST_API_URL,
                headers=HEADERS,
                json={
                    "files": {"verifications.json": {"content": updated.model_dump_json(indent=2)}}
                },
            )
        logging.info("Verification counter updated", extra={"total": updated.total})
    except Exception as e:
        logging.error("Failed to update verification gist", extra={"error": str(e)})
