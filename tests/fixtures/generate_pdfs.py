"""Generate synthetic test PDFs for end-to-end pipeline tests.

Born-digital PDFs (text is in the PDF as text, not as a raster), so we can
extract content with pypdf and skip OCR latency in the tests. Real-world
documents come in as scanned rasters and need the OCR pass; that path is
exercised in a separate, slower test suite.

Templates are deliberately ugly and minimal — we are not selling these as
realistic-looking documents, we're seeding text content that exercises
the classifier and extractor. If you want pretty fixtures, render real
forms; this is for the test bench.

Run from the project root:
    python -m tests.fixtures.generate_pdfs

Idempotent: regenerating overwrites in place. Files are committed so the
tests have no generation step in CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).parent / "synthetic"


@dataclass(frozen=True)
class FixtureDoc:
    """A specification for one generated PDF.

    `expected_doc_type` and `expected_vendor` are used by end-to-end
    tests to assert the pipeline classifies this fixture correctly.
    """

    filename: str
    expected_doc_type: str
    expected_vendor: str | None
    lines: tuple[str, ...]


# A small but representative corpus. Covers:
# - All five _base.yaml doc types
# - Two BOLs (generic + vendor-overridden Heineken)
# - One PoD
# - One prescription (cross-industry sanity check)
FIXTURES: tuple[FixtureDoc, ...] = (
    FixtureDoc(
        filename="invoice_acme.pdf",
        expected_doc_type="invoice",
        expected_vendor=None,
        lines=(
            "ACME WHOLESALE CO",
            "123 Industrial Way",
            "INVOICE",
            "Invoice #: INV-12345",
            "Bill To: Northern Distributors",
            "Date: 03/15/2026",
            "Description       Qty    Price",
            "Widgets            10    $50.00",
            "Gadgets             5    $20.00",
            "Amount Due: $600.00",
            "Payment Terms: Net 30",
        ),
    ),
    FixtureDoc(
        filename="invoice_globex.pdf",
        expected_doc_type="invoice",
        expected_vendor=None,
        lines=(
            "GLOBEX CORPORATION",
            "INVOICE",
            "Invoice Number: INV-99001",
            "Bill To: Springfield Office Supply",
            "Date: 04/02/2026",
            "Total: $1,234.56",
            "Payment Terms: Net 60",
            "Remittance to: P.O. Box 4421",
        ),
    ),
    FixtureDoc(
        filename="bol_heineken.pdf",
        expected_doc_type="bol",
        expected_vendor="Heineken",
        lines=(
            "HEINEKEN USA",
            "BILL OF LADING",
            "PO #: HK-44218",
            "Shipper: HEINEKEN USA",
            "Consignee: KDC Warehouse",
            "SCAC: HEIN",
            "Seal Number: 22817",
            "Date: 03/20/2026",
            "Cases: 480",
        ),
    ),
    FixtureDoc(
        filename="bol_generic.pdf",
        expected_doc_type="bol",
        expected_vendor=None,
        lines=(
            "Sample Freight Carriers",
            "STRAIGHT BILL OF LADING",
            "BOL Number: BOL-987654",
            "Shipper: Sample Manufacturing",
            "Consignee: Receiving Dock 7",
            "Date: 02/11/2026",
            "Pieces: 12   Weight: 4400 lb",
        ),
    ),
    FixtureDoc(
        filename="pod_delivery.pdf",
        expected_doc_type="pod",
        expected_vendor=None,
        lines=(
            "PROOF OF DELIVERY",
            "Customer Signature: J. Smith",
            "Delivery Date: 03/22/2026",
            "Cases Received: 24",
            "Pallets Received: 1",
            "Received By: Warehouse Mgr",
        ),
    ),
    FixtureDoc(
        filename="receipt_coffee.pdf",
        expected_doc_type="receipt",
        expected_vendor=None,
        lines=(
            "Daily Grind Coffee",
            "RECEIPT",
            "Item            Price",
            "Latte           $5.50",
            "Pastry          $3.75",
            "Total: $9.25",
            "Amount Paid: $10.00",
            "Change Due: $0.75",
            "Thank you!",
        ),
    ),
    FixtureDoc(
        filename="statement_bank.pdf",
        expected_doc_type="statement",
        expected_vendor=None,
        lines=(
            "FIRST NATIONAL BANK",
            "ACCOUNT STATEMENT",
            "Statement Period: 03/01/2026 - 03/31/2026",
            "Beginning Balance: $12,000.00",
            "Total Deposits: $4,500.00",
            "Total Withdrawals: $3,210.00",
            "Ending Balance: $13,290.00",
        ),
    ),
    FixtureDoc(
        filename="po_office_supplies.pdf",
        expected_doc_type="purchase_order",
        expected_vendor=None,
        lines=(
            "OFFICEMAX BUSINESS",
            "PURCHASE ORDER",
            "PO #: PO-7788",
            "Buyer: City Council",
            "Ship To: 100 Main Street",
            "Item              Qty    Price",
            "Letterhead        500    $0.15",
            "Total: $75.00",
        ),
    ),
    FixtureDoc(
        filename="prescription_lisinopril.pdf",
        expected_doc_type="prescription",
        expected_vendor=None,
        lines=(
            "Rx",
            "Patient: J. Doe",
            "DOB: 1972-04-15",
            "Drug: Lisinopril 10mg",
            "SIG: Take 1 tablet daily",
            "Dispense: 30 tablets",
            "Refills: 5",
            "Prescriber: Dr. Allen Walsh",
            "NPI: 1234567890",
            "DEA #: AB1234567",
        ),
    ),
    FixtureDoc(
        filename="contract_lease.pdf",
        expected_doc_type="contract",
        expected_vendor=None,
        lines=(
            "OFFICE LEASE AGREEMENT",
            "THIS CONTRACT is entered into between Landlord LLC and Tenant Co.",
            "WHEREAS the parties wish to enter into a lease agreement,",
            "Effective Date: 05/01/2026",
            "Termination: 04/30/2027",
            "Governing Law: State of Texas",
        ),
    ),
)


def _render_pdf(path: Path, lines: tuple[str, ...]) -> None:
    """Write one PDF with each line of text on its own row.

    Layout is intentionally minimal — top-down list. We're testing text
    content extraction, not layout parsing.
    """
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER
    y = height - 72  # one inch margin
    c.setFont("Helvetica", 11)
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
        if y < 72:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 72
    c.save()


def generate_all(out_dir: Path = OUT_DIR) -> list[Path]:
    """Generate every fixture. Returns the list of paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fixture in FIXTURES:
        path = out_dir / fixture.filename
        _render_pdf(path, fixture.lines)
        paths.append(path)
    return paths


def fixture_for(filename: str) -> FixtureDoc:
    """Look up a fixture by filename — used in test assertions."""
    for f in FIXTURES:
        if f.filename == filename:
            return f
    raise KeyError(filename)


if __name__ == "__main__":
    written = generate_all()
    for p in written:
        print(f"wrote {p}")
