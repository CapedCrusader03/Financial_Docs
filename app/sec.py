"""EDGAR client. The submissions feed selects a filing; companyfacts supplies XBRL."""
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.config import settings

DATA_BASE = "https://data.sec.gov"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


@dataclass(frozen=True)
class FilingRef:
    cik: str
    ticker: str
    company: str
    form: str
    accession: str
    primary_document: str
    report_date: str
    filed_date: date

    @property
    def html_url(self) -> str:
        return f"{ARCHIVES_BASE}/{int(self.cik)}/{self.accession.replace('-', '')}/{self.primary_document}"


class EdgarClient:
    def __init__(self) -> None:
        self.headers = {"User-Agent": settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}

    def _get_json(self, url: str) -> dict[str, Any]:
        response = httpx.get(url, headers=self.headers, timeout=45)
        response.raise_for_status()
        return response.json()

    def resolve_ticker(self, ticker: str) -> tuple[str, str]:
        payload = self._get_json("https://www.sec.gov/files/company_tickers.json")
        wanted = ticker.upper()
        for row in payload.values():
            if row["ticker"].upper() == wanted:
                return str(row["cik_str"]).zfill(10), row["title"]
        raise ValueError(f"Ticker {ticker!r} was not found in SEC company tickers.")

    def list_filings(self, ticker: str, form: str) -> list[FilingRef]:
        cik, company = self.resolve_ticker(ticker)
        submissions = self._get_json(f"{DATA_BASE}/submissions/CIK{cik}.json")
        rows = submissions["filings"]["recent"]
        result: list[FilingRef] = []
        for index, candidate_form in enumerate(rows["form"]):
            if candidate_form != form:
                continue
            primary = rows["primaryDocument"][index]
            # Some index-only entries cannot provide meaningful narrative HTML.
            if not primary:
                continue
            result.append(FilingRef(
                cik=cik, ticker=ticker.upper(), company=company, form=form,
                accession=rows["accessionNumber"][index], primary_document=primary,
                report_date=rows["reportDate"][index],
                filed_date=date.fromisoformat(rows["filingDate"][index]),
            ))
        return result

    def select_filing(self, ticker: str, form: str, accession: str | None = None) -> FilingRef:
        filings = self.list_filings(ticker, form)
        if accession:
            for filing in filings:
                if filing.accession == accession:
                    return filing
            raise ValueError(f"{accession} is not a recent {form} for {ticker}.")
        if not filings:
            raise ValueError(f"No recent {form} was found for {ticker}.")
        return filings[0]

    def fetch_primary_html(self, filing: FilingRef) -> str:
        response = httpx.get(filing.html_url, headers=self.headers, timeout=60)
        response.raise_for_status()
        return response.text

    def company_facts(self, cik: str) -> dict[str, Any]:
        return self._get_json(f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json")
