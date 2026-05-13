# CLAUDE.md — Doc Sherpa

Distilled from the kickoff brief. Read this every session before touching code.

## What this POC proves

A self-hosted document pipeline that is:

1. Set up by anyone in under an hour without writing regex
2. Configured via plugins for any destination (S3, Google Drive, SharePoint, FS)
3. Self-improving via operator approvals (learning loop)
4. Maintainable without ongoing sysadmin involvement after setup

The **differentiating piece** is the training service. The runtime engine
that consumes the YAML is commodity infrastructure — don't over-invest there.

## Architecture

```
training-service/    FastAPI: upload + label + iterate + output YAML
runtime-engine/      daemon: watch + OCR + classify + route        (phase 2)
shared/protocols/    plugin Protocols                                (Python typing.Protocol)
shared/types.py      DocumentType enum, dataclasses
packs/               industry pack YAML (beverage_distributor.yaml, ...)
plugins/             plugin implementations
docs/                design notes
docs/_reference/     scan-to-sharepoint excerpts (gitignored; READ-ONLY)
tests/
```

Directory names use hyphens (`training-service/`). Python packages inside
use underscores (`training_service/`). Hyphens are not valid Python
identifiers; this is the cheapest reconciliation.

## Plugin categories (POC priority order)

1. **DocumentDestination** — local filesystem first, S3 second, Google Drive third, SharePoint last
2. **AIClassifier** — Anthropic Claude (used by both services)
3. **NotificationSink** — webhook (Slack/generic), email later
4. **DocumentIngest** — watch folder first, HTTP upload API second

**NOT abstracted yet** (premature for POC scope):

- Database — SQLite, single implementation
- OCR — tesseract via `ocrmypdf`, single implementation
- Auth — no auth in POC; LAN-only or env-var token

When the abstraction is one-of-one, the Protocol is friction without value.
Promote to Protocol only when a second implementation actually arrives.

## Cohesion rule (CRITICAL — read this when a file is growing)

> One responsibility per module. If you can name the file with a single noun
> phrase ("classifier", "graph store", "ai client"), it's probably one
> responsibility. If naming it requires "and," it's two things — split.

- Triage on **cohesion**, not line count. A module growing because it's
  getting feature-complete in one domain is fine. A module growing because
  it's accumulating unrelated concerns is not.
- The reference repo (`docs/_reference/scan_to_sharepoint.py`) is a negative
  example at 3494 lines: it's the event log AND the review queue AND the
  knowledge graph AND the classifier AND the pipeline orchestrator. Split
  *those*, not lines.
- When a file is growing, pause before adding the next feature and ask
  "is this still one thing?" The 200th line is when decisions calcify.

## Code quality standards

- Every public function has a docstring explaining **intent** (why, not what).
- Type hints everywhere; `Any` only with an inline `# Any: reason` comment.
- Plugin interface tests use **fakes**, not mocks.
- One service-level test per major flow.
- Target 70%+ coverage on new code. Coverage is a measure of conscientiousness,
  not a build gate.

## Phase 1 deliverables (training-service)

Build the training service end-to-end before touching the runtime engine.

1. `training-service/training_service/main.py` — FastAPI app skeleton
2. **Upload endpoint** — accept 20–30 PDFs, store in temp dir keyed by session ID
3. **Labeling UI** — minimal HTML, one row per document, fields:
   `doc_type` (dropdown of 5 categories), `vendor`, `invoice_or_bol_number`, `customer`
4. **Iteration endpoint** — take labels + docs, OCR, ask Claude Sonnet to
   synthesize regex patterns, score against labels.
   - **Hold back 20% of the corpus as a never-seen validation set.**
   - **Stop iterating when validation match-rate stops improving** — not
     when training match-rate hits the ceiling. Prevents overfitting.
5. **Output endpoint** — download generated `classifier.yaml`
6. **Session cleanup** — temp files deleted after output or after 24h,
   whichever first

Model: `claude-sonnet-4-5`. Track and log cost per session.
Budget target: full training run under $10 in Sonnet usage.

## Document type vocabulary (from the reference, 5 categories)

| Type        | Description                                              |
|-------------|----------------------------------------------------------|
| `trade_out` | Trade-out forms (close-dated product, distinctive header) |
| `bol`       | Bill of Lading / freight bill / packing list             |
| `pod`       | Proof of Delivery / delivery receipt                     |
| `donation`  | Donation forms (often look like invoices)                |
| `invoice`   | Outgoing invoices, statements, remittances               |

Order matters in the existing rules: `trade_out` → `bol` → `pod` → `donation` → `invoice`.
That order is *learned* from the customer's labeled samples, not hardcoded —
the training service should rediscover this for each customer.

## What NOT to build in phase 1

- The runtime engine (phase 2)
- Setup wizard (phase 3)
- SharePoint plugin or any destination plugin yet
- Real auth — env-var token is fine
- Multi-tenant DB schema — assume one user per service instance
- Billing

## Definition of done for the POC overall

A first-time user can:

1. Run `docker compose up` on the runtime engine and get a working pipeline
2. Visit the training service, upload sample docs, get a `classifier.yaml`
3. Drop that YAML into the runtime engine's config and have classification
   working on their real scans

If a **non-technical** operator can do steps 2 and 3 without help, the POC
has demonstrated its value.

## Reference repo (read-only)

`github.com/larpey/scan-to-sharepoint` (private). Cached excerpts in
`docs/_reference/` (gitignored).

Useful files:

- `scan_to_sharepoint.py` — monolithic core. Study `DOC_TYPE_RULES`,
  `BOL_VENDOR_SIGNATURES`, `BOL_NUMBER_EXTRACTORS` — these are the kind of
  rules the training service will GENERATE for new customers.
- `bol_ai_fallback.py` — already plugin-ish; the `AIClassifier` plugin will
  look similar in shape.
- `dashboard.py` + `dashboard.html` — overgrown. Reference for review-queue
  UX, but build something far smaller.

**DO NOT copy code. Port patterns, not lines.**

## Swarm topology

Phase 1: `planner` + `training-service-agent` + `test-writer` + `reviewer` + `doc-writer`.
Add `runtime-engine-agent` and `plugin-builder` in phase 2.

Shared memory namespaces:

- `arch.*` — plugin protocols, module boundaries, naming conventions
- `spec.*` — phase deliverables, definition of done
- `decisions.*` — design choices with rationale (why X over Y)

Before starting any non-trivial task, query `arch.*` and `decisions.*` for
relevant entries. If a decision needs to be made that isn't in memory, ask
in chat and persist the answer once made.

## Running phase 1

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -e .[dev]
uvicorn training_service.main:app --app-dir training-service --reload --port 8001
```

Visit <http://localhost:8001/>.
