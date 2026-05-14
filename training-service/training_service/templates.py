"""HTML rendering for the labeling UI. Plain f-strings; no Jinja dependency.

Why no template engine: the UI surface is three pages (home, upload, label,
done = four small pages) and adding Jinja2 buys little. When we cross five
pages or need partials, swap to Jinja. Today, html.escape + f-strings keep
the rendering local and obvious.

All user-visible strings flow through `html.escape` at the boundary —
filenames especially, since they're attacker-controllable. If you add a
new template here, do the same.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from .documents import Document
from .labels import StoredLabel
from .sessions import Session

# CSS lives inline so the service has zero static-asset dependencies for
# the POC. Move to a real stylesheet when the UI grows.
_STYLE = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 880px;
       margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; color: #444; }
.subtitle { color: #666; margin-top: 0; }
form.row { padding: 1rem; border: 1px solid #ddd; border-radius: 6px;
           margin-bottom: 1rem; background: #fafafa; }
form.row .filename { font-weight: 600; }
form.row .meta { color: #888; font-size: 0.85rem; margin: 0.25rem 0 0.75rem; }
label { display: inline-block; min-width: 7rem; margin-right: 0.5rem; }
input[type="text"], select { padding: 0.3rem 0.5rem; min-width: 14rem; }
.actions { margin-top: 1.5rem; }
button { padding: 0.5rem 1rem; font-size: 1rem; cursor: pointer; }
table { border-collapse: collapse; }
table td, table th { padding: 0.25rem 0.6rem; border-bottom: 1px solid #eee; }
.nav a { color: #0366d6; text-decoration: none; }
.nav a:hover { text-decoration: underline; }
.empty { color: #888; font-style: italic; }
"""


def _page(title: str, body: str) -> str:
    """Wrap a body fragment in a minimal HTML document."""
    return (
        "<!doctype html><html lang='en'><head>"
        f"<meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def render_home(recent_sessions: list[Session]) -> str:
    """Landing page: start a new session, or resume a recent one."""
    rows = ""
    if recent_sessions:
        rows = "<h2>Recent sessions</h2><table><tr><th>ID</th><th>Created (UTC)</th><th>Status</th><th></th></tr>"
        for s in recent_sessions:
            rows += (
                f"<tr><td><code>{escape(s.id[:8])}</code></td>"
                f"<td>{escape(s.created_at.strftime('%Y-%m-%d %H:%M'))}</td>"
                f"<td>{escape(s.status)}</td>"
                f"<td class='nav'><a href='/sessions/{escape(s.id)}/label'>open</a></td></tr>"
            )
        rows += "</table>"
    else:
        rows = "<p class='empty'>No sessions yet.</p>"

    body = f"""
    <h1>Doc Sherpa — training service</h1>
    <p class='subtitle'>Upload sample documents, label them, and (soon) get a
       <code>classifier.yaml</code> tuned to your corpus.</p>
    <form method='post' action='/sessions'>
      <button type='submit'>Start new training session</button>
    </form>
    {rows}
    """
    return _page("Doc Sherpa — training service", body)


def render_session_upload(session: Session) -> str:
    """Per-session upload page. Accepts multiple PDFs in one form post."""
    body = f"""
    <h1>Upload documents</h1>
    <p class='subtitle'>Session <code>{escape(session.id[:8])}</code> · created
       {escape(session.created_at.strftime('%Y-%m-%d %H:%M UTC'))}</p>
    <form method='post' action='/sessions/{escape(session.id)}/documents'
          enctype='multipart/form-data'>
      <p><input type='file' name='files' accept='application/pdf' multiple required></p>
      <div class='actions'>
        <button type='submit'>Upload</button>
        <span style='margin-left:1rem'>
          <a class='nav' href='/sessions/{escape(session.id)}/label'>or skip to labeling →</a>
        </span>
      </div>
    </form>
    <p class='subtitle' style='margin-top:2rem'>Tip: 20–30 samples gives the
       best training signal. PDFs only, 50&nbsp;MiB per file.</p>
    """
    return _page("Upload — training service", body)


def _doc_type_options(doc_types: list[str], selected: str | None) -> str:
    """Render <option> tags for the doc-type dropdown.

    Options come from the live KB, not a hardcoded enum — so a clinic install
    shows `prescription` etc., a logistics install shows `bol`, and so on.
    """
    options = ""
    for name in doc_types:
        sel = " selected" if selected and name == selected else ""
        options += f"<option value='{escape(name)}'{sel}>{escape(name)}</option>"
    return options


def _input(name: str, value: str | None, placeholder: str = "") -> str:
    v = escape(value) if value else ""
    return (
        f"<input type='text' name='{escape(name)}' value='{v}' "
        f"placeholder='{escape(placeholder)}'>"
    )


def render_session_label(
    session: Session,
    pairs: list[tuple[Document, StoredLabel | None]],
    doc_types: list[str],
) -> str:
    """Labeling form: one row per uploaded document.

    The form serializes label fields keyed by document ID (`doc_type__<id>`,
    `vendor__<id>`, etc.) so the route handler can iterate by document. This
    is uglier than an array of objects but simpler than building a JSON body
    out of a bare HTML form.
    """
    if not pairs:
        body = f"""
        <h1>Labeling</h1>
        <p class='subtitle'>Session <code>{escape(session.id[:8])}</code></p>
        <p class='empty'>No documents uploaded yet.
           <a href='/sessions/{escape(session.id)}/upload' class='nav'>Upload some →</a></p>
        """
        return _page("Labeling — training service", body)

    rows = ""
    for doc, label in pairs:
        rows += f"""
        <fieldset class='row'>
          <div class='filename'>{escape(doc.original_filename)}</div>
          <div class='meta'>
            id <code>{escape(doc.id[:8])}</code>
            · {doc.size_bytes // 1024} KiB
            · uploaded {escape(doc.uploaded_at.strftime('%Y-%m-%d %H:%M UTC'))}
          </div>
          <div>
            <label for='dt-{escape(doc.id)}'>doc type</label>
            <select id='dt-{escape(doc.id)}' name='doc_type__{escape(doc.id)}' required>
              {_doc_type_options(doc_types, label.doc_type if label else None)}
            </select>
          </div>
          <div>
            <label>vendor</label>
            {_input(f'vendor__{doc.id}', label.vendor if label else None, 'e.g. Heineken')}
          </div>
          <div>
            <label>invoice / BOL #</label>
            {_input(f'identifier__{doc.id}', label.identifier if label else None, 'e.g. W-1234567')}
          </div>
          <div>
            <label>customer</label>
            {_input(f'customer__{doc.id}', label.customer if label else None, 'e.g. Total Wine')}
          </div>
        </fieldset>
        """

    body = f"""
    <h1>Label documents</h1>
    <p class='subtitle'>Session <code>{escape(session.id[:8])}</code> ·
       {len(pairs)} document(s)</p>
    <form method='post' action='/sessions/{escape(session.id)}/labels'>
      {rows}
      <div class='actions'>
        <button type='submit'>Save labels</button>
        <span style='margin-left:1rem'>
          <a class='nav' href='/sessions/{escape(session.id)}/upload'>← add more files</a>
        </span>
      </div>
    </form>
    """
    return _page("Labeling — training service", body)


def render_session_done(session: Session, label_count: int, now: datetime) -> str:
    """Acknowledgement page after the operator submits labels."""
    body = f"""
    <h1>Labels saved</h1>
    <p class='subtitle'>Session <code>{escape(session.id[:8])}</code> ·
       {label_count} label(s) recorded at
       {escape(now.strftime('%Y-%m-%d %H:%M UTC'))}.</p>
    <p>The training step (regex synthesis via Claude Sonnet) is the next
       deliverable — it isn't wired up yet. When it is, you'll be able to
       download a <code>classifier.yaml</code> from this page.</p>
    <p class='nav'>
      <a href='/sessions/{escape(session.id)}/label'>← edit labels</a>
      &nbsp;·&nbsp;
      <a href='/'>home</a>
    </p>
    """
    return _page("Done — training service", body)
