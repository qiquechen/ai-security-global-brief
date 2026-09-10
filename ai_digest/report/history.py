"""Fetch a historical day for just the underfilled source categories."""
from .. import config
from ..ingest.crawler import load_sources


def backfill_history(start, end, groups):
    from ..ingest.run import main
    sources = load_sources(config.ROOT / "config" / "sources.json")
    ids = [s.id for s in sources if s.enabled and
           ("media" if s.type == "media" else "institution") in groups]
    if not ids:
        return {"exit_code": 2, "sources": 0}
    arguments = ["--days", str((end - start).total_seconds() / 86400),
                 "--at", end.isoformat()]
    for source_id in ids:
        arguments.extend(["--source", source_id])
    return {"exit_code": main(arguments), "sources": len(ids)}
