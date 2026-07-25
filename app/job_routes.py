# app/job_routes.py
"""Endpointy zadań w tle — kontrakt wspólny z games-api i sleeves-api."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import jobs

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("")
async def list_jobs():
    """Stan wszystkich zadań — jedno zapytanie zasila cały ekran w aplikacji."""
    return {"jobs": await jobs.status_all()}


@router.get("/{job}")
async def job_status(job: str):
    try:
        return await jobs.status(job)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job}")


@router.post("/{job}/run")
async def job_run(job: str):
    """Uruchamia zadanie w tle i NATYCHMIAST wraca (klient nie czeka minutami).

    409 = zadanie już trwa; zwracamy jego status, żeby aplikacja mogła się
    do niego podłączyć zamiast pokazywać błąd.
    """
    try:
        _record, accepted = await jobs.start(job, trigger="manual")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job}")
    payload = await jobs.status(job)
    return JSONResponse(status_code=202 if accepted else 409, content=payload)


@router.post("/{job}/cancel")
async def job_cancel(job: str):
    """Kooperatywne anulowanie — zadanie zatrzyma się na najbliższym sprawdzeniu."""
    try:
        await jobs.request_cancel(job)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job}")
    return await jobs.status(job)
