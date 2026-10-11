"""Resolve the `read` argument into content to feed a reader."""

from __future__ import annotations

import hashlib
import re

PMCID_RE = re.compile(r"^PMC\d+$", re.IGNORECASE)
PMID_RE = re.compile(r"^\d+$")


def resolve(target: str) -> tuple[str, str, str]:
    """Return (run_id, kind, content) where kind is 'nxml' or 'text'.

    - ``PMC\\d+``  -> full-text NXML from PMC OA
    - ``\\d+``     -> PubMed abstract text for that PMID
    - anything else -> the string itself, as raw text
    """
    target = target.strip()

    if PMCID_RE.match(target):
        from indra.literature import pmc_client

        pmcid = target.upper()
        xml = _efetch_pmc_nxml(pmcid)
        if not xml:
            # OAI-PMH fallback: OA-subset only and rate-limited, but sometimes works.
            xml = pmc_client.get_xml(pmcid)
        if not xml:
            raise RuntimeError(
                f"no full-text XML available from PMC for {pmcid} "
                "(not in the open-access subset, or PMC is unreachable)"
            )
        return pmcid, "nxml", xml

    if PMID_RE.match(target):
        from indra.literature import pubmed_client

        abstract = pubmed_client.get_abstract(target)
        if not abstract:
            raise RuntimeError(
                f"no abstract available from PubMed for PMID {target}"
            )
        return target, "text", abstract

    digest = hashlib.sha1(target.encode("utf-8")).hexdigest()[:8]
    return f"text-{digest}", "text", target


PMID_IN_NXML_RE = re.compile(
    r'<article-id[^>]*pub-id-type="pmid"[^>]*>\s*(\d+)\s*</article-id>'
)


def pmid_from_nxml(xml: str) -> str:
    """The PMID carried in the article's own front matter, or ''."""
    match = PMID_IN_NXML_RE.search(xml)
    return match.group(1) if match else ""


def pmid_for_pmcid(pmcid: str) -> str:
    """PMID for a PMC id via NCBI's id converter, or '' if it cannot be resolved."""
    from indra.literature import pmc_client

    try:
        ids = pmc_client.id_lookup(pmcid, "pmcid") or {}
    except Exception:  # noqa: BLE001 - id resolution is best-effort
        return ""
    return str(ids.get("pmid") or "")


EFETCH_URL ="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _efetch_pmc_nxml(pmcid: str) -> str | None:
    """Full-text NXML for a PMC id via NCBI efetch, or None if unavailable."""
    import requests

    try:
        resp = requests.get(
            EFETCH_URL,
            params={"db": "pmc", "id": pmcid, "rettype": "xml"},
            timeout=60,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200 or "<article" not in resp.text:
        return None
    return resp.text


def nxml_to_text(xml: str) -> str:
    """Best-effort plain text from a PMC NXML document."""
    from indra.literature import pmc_client

    text = pmc_client.extract_text(xml)
    if not text:
        raise RuntimeError("could not extract plain text from the PMC XML")
    return text
