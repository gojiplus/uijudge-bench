"""Deterministic accessibility auditing via a vendored axe-core bundle.

Vendored and trimmed from ``layoutlens/a11y/`` (MIT). Injects the bundled axe-core
JavaScript into a Playwright page, runs ``axe.run``, and maps the JSON into typed
:class:`A11yReport` objects. The axe assets live under ``vendor/assets`` and are loaded
via :mod:`importlib.resources`, so they work from an installed wheel.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .browser import open_page

logger = logging.getLogger("uijudge.vendor.a11y")

# Exact version of the vendored axe-core bundle (see assets/axe.min.js header).
AXE_VERSION = "4.10.3"

MAX_HTML_LEN = 200
_STANDARD_TAG_PREFIXES = ("wcag", "section508")


@lru_cache(maxsize=1)
def _load_axe_source() -> str:
    """Load and cache the vendored axe-core JavaScript source."""
    asset = resources.files("uijudge.vendor") / "assets" / "axe.min.js"
    return asset.read_text(encoding="utf-8")


def _truncate_html(html: str) -> str:
    """Truncate an HTML snippet to a sane length for reporting."""
    return html if len(html) <= MAX_HTML_LEN else html[:MAX_HTML_LEN] + "..."


def _filter_wcag_refs(tags: list[str]) -> list[str]:
    """Keep only standards tags (wcag*, section508), dropping category tags."""
    return [tag for tag in tags if tag.startswith(_STANDARD_TAG_PREFIXES)]


@dataclass(slots=True)
class A11yFinding:
    """A single accessibility rule outcome affecting one or more DOM nodes."""

    rule_id: str
    impact: str
    wcag_refs: list[str]
    description: str
    help_url: str
    nodes: list[dict[str, Any]]
    engine: str = "axe-core"


@dataclass(slots=True)
class A11yReport:
    """Structured accessibility report for a single page and viewport."""

    source: str
    viewport: str
    engine_version: str
    violations: list[A11yFinding]
    incomplete: list[A11yFinding]
    passes_count: int
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def ok(self) -> bool:
        """Return True if there are no violations."""
        return len(self.violations) == 0

    def to_json(self) -> str:
        """Serialize the report to an indented JSON string."""
        return json.dumps(asdict(self), indent=2, default=str)


def _finding_from_rule(rule: dict[str, Any]) -> A11yFinding:
    """Map a single axe rule result (violation/incomplete) to an A11yFinding."""
    nodes = [
        {"target": node.get("target", []), "html": _truncate_html(node.get("html", ""))}
        for node in rule.get("nodes", [])
    ]
    return A11yFinding(
        rule_id=rule.get("id", ""),
        impact=rule.get("impact") or "",
        wcag_refs=_filter_wcag_refs(rule.get("tags", [])),
        description=rule.get("description", ""),
        help_url=rule.get("helpUrl", ""),
        nodes=nodes,
    )


class AxeAuditor:
    """Runs axe-core against a Playwright page and returns structured findings.

    Args:
        run_only: Optional list of axe tags to restrict the run to (e.g.
            ``["wcag2a", "wcag2aa"]``). ``None`` uses axe defaults (all rules).
        disabled_rules: Optional list of rule ids to disable.
    """

    def __init__(self, run_only: list[str] | None = None, disabled_rules: list[str] | None = None):
        """Initialize the auditor with optional tag and rule filters."""
        self.run_only = run_only
        self.disabled_rules = disabled_rules or []

    def _axe_options(self) -> dict[str, Any]:
        """Build the options dict passed to ``axe.run``."""
        options: dict[str, Any] = {}
        if self.run_only:
            options["runOnly"] = {"type": "tag", "values": self.run_only}
        if self.disabled_rules:
            options["rules"] = {rule_id: {"enabled": False} for rule_id in self.disabled_rules}
        return options

    @staticmethod
    def _build_report(results: dict[str, Any], source: str, viewport: str) -> A11yReport:
        """Map a raw ``axe.run`` results dict into an :class:`A11yReport`."""
        violations = [_finding_from_rule(rule) for rule in results.get("violations", [])]
        incomplete = [_finding_from_rule(rule) for rule in results.get("incomplete", [])]
        engine_version = results.get("testEngine", {}).get("version") or AXE_VERSION
        return A11yReport(
            source=source,
            viewport=viewport,
            engine_version=engine_version,
            violations=violations,
            incomplete=incomplete,
            passes_count=len(results.get("passes", [])),
        )

    async def audit_page(self, page: Page, source: str | None = None, viewport: str = "desktop") -> A11yReport:
        """Inject axe-core into an already-loaded page and run the audit.

        Args:
            page: A loaded Playwright page.
            source: Optional source label recorded in the report; defaults to the URL.
            viewport: Viewport name recorded in the report.

        Returns:
            The structured accessibility report.

        Raises:
            RuntimeError: If axe injection or execution fails.
        """
        source_label = source if source is not None else page.url
        try:
            await page.add_script_tag(content=_load_axe_source())
            results = await page.evaluate("(opts) => axe.run(document, opts)", self._axe_options())
        except Exception as exc:  # noqa: BLE001 - re-wrapped into a domain error
            raise RuntimeError(f"axe-core execution failed for {source_label}: {exc}") from exc
        return self._build_report(results, source_label, viewport)

    async def audit(self, source: str | Path, viewport: str = "desktop") -> A11yReport:
        """Audit a URL or local HTML file, owning the browser lifecycle.

        Args:
            source: A URL or path to a local HTML file.
            viewport: Viewport name.

        Returns:
            The structured accessibility report.
        """
        async with open_page(source, viewport) as page:
            return await self.audit_page(page, source=str(source), viewport=str(viewport))
