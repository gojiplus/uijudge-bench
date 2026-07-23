"""Corpus ingestion modules (the ``ingested`` door). One module per upstream source.

Import submodules explicitly (``from uijudge.engine.ingest import act``) rather than
eagerly here, so ``python -m uijudge.engine.ingest.<source>`` runs without a re-import
warning and unused sources are not imported.
"""
