"""AccessGuru ingestion — download-at-build (the ``ingested`` door).

License investigation (verified via the DaRUS dataset API): the AccessGuru dataset
(DOI ``10.18419/DARUS-5177``) is licensed **CC BY 4.0** — redistribution and adaptation
permitted with attribution. We *may* redistribute, but the dataset is ~552 MB (dominated
by a 494 MB screenshot archive), so we ship this download-at-build script instead of the
data (the "LAION model"): the useful ~20 MB tabular file is fetched on demand into the
git-ignored ``corpus/_downloads/`` directory. See ``docs/LICENSING.md``.

**P1 status:** license resolved; a runnable downloader is provided. Parsing the 3,524
rows into schema-valid items (mapping each violation to a WCAG SC + anchor + receipt) is
P2 scale work — this module deliberately does not write label lines yet, to avoid landing
half-mapped items. ``run()`` records the license finding in a report; ``run(download=True)``
additionally fetches the tabular file so the P2 mapping work can begin from local data.

Attribution (required by CC BY 4.0): Fathallah, N., Hernández, D., & Staab, S.,
"AccessGuru: Leveraging LLMs to Detect and Correct Web Accessibility Violations in HTML
Code", DaRUS, DOI 10.18419/DARUS-5177, CC BY 4.0.
"""

from __future__ import annotations

import argparse

import httpx

from ._common import CORPUS_DIR, IngestStats, iso_today, write_report

DOI = "doi:10.18419/DARUS-5177"
SOURCE = "accessguru"
LICENSE = "CC BY 4.0"
LICENSE_URL = "http://creativecommons.org/licenses/by/4.0"
DATAVERSE = "https://darus.uni-stuttgart.de"
DATASET_API = f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={DOI}"

# Tabular file (id resolved via the dataset API): Original_full_data_new (~20 MB, 3524 rows).
TABULAR_FILE_ID = 415640
TABULAR_FILENAME = "Original_full_data_new.tab"
ACCESS_URL = f"{DATAVERSE}/api/access/datafile/{TABULAR_FILE_ID}"

DOWNLOAD_DIR = CORPUS_DIR / "_downloads" / "accessguru"


def _verify_license() -> dict:
    """Query the DaRUS API and return the dataset's declared license block.

    Returns:
        The license dict (e.g. ``{"name": "CC BY 4.0", ...}``).

    Raises:
        RuntimeError: If the API is unreachable or returns an unexpected shape.
    """
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(DATASET_API, timeout=60.0)
        resp.raise_for_status()
        return resp.json()["data"]["latestVersion"].get("license", {})


def download_tabular() -> str:
    """Download the AccessGuru tabular file into the git-ignored downloads dir.

    Returns:
        The local path to the downloaded file, as a string.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / TABULAR_FILENAME
    with httpx.Client(follow_redirects=True) as client, client.stream("GET", ACCESS_URL, timeout=300.0) as resp:
        resp.raise_for_status()
        with target.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
    return str(target)


def run(download: bool = False) -> IngestStats:
    """Record the AccessGuru license finding; optionally download the tabular file.

    Args:
        download: If True, fetch the ~20 MB tabular file into ``corpus/_downloads/``.

    Returns:
        The ingestion stats (0 label lines in P1 by design).
    """
    date = iso_today()
    stats = IngestStats(source=SOURCE, retrieval_date=date)
    stats.notes.update(
        doi=DOI,
        license=LICENSE,
        license_url=LICENSE_URL,
        redistributable=True,
        decision="download-at-build (LAION model): 552 MB dataset not committed; ship the script",
        p1_status="license verified; label mapping deferred to P2",
        attribution="Fathallah, Hernández, Staab (2024), DaRUS DOI 10.18419/DARUS-5177, CC BY 4.0",
    )

    try:
        declared = _verify_license()
        stats.notes["declared_license"] = declared.get("name")
        if declared.get("name") and declared["name"] != LICENSE:
            stats.notes["license_mismatch_warning"] = (
                f"declared '{declared.get('name')}' differs from recorded '{LICENSE}'"
            )
    except Exception as exc:  # noqa: BLE001 - offline runs still produce the report
        stats.notes["license_check"] = f"skipped/failed (offline?): {exc}"

    if download:
        try:
            stats.notes["downloaded_to"] = download_tabular()
        except Exception as exc:  # noqa: BLE001
            stats.notes["download_error"] = str(exc)

    write_report("ingest_accessguru", stats)
    return stats


def main() -> int:
    """CLI entry point for ``python -m uijudge.engine.ingest.accessguru``."""
    parser = argparse.ArgumentParser(description="AccessGuru license check + download-at-build.")
    parser.add_argument("--download", action="store_true", help="Download the ~20 MB tabular file.")
    args = parser.parse_args()
    stats = run(download=args.download)
    print(f"[accessguru] license={stats.notes.get('declared_license') or LICENSE} status={stats.notes['p1_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
