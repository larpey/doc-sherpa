"""Inbox HTML. Plain f-strings + html.escape. One page for the POC.

Page renders the most recent classifications with:
- source filename
- proposed doc type + confidence
- where it landed
- signals that contributed (operator can see *why*)
- Accept button + Correct form (dropdown of KB doc types + vendor field)
"""

from __future__ import annotations

from html import escape

from .log import LogEntry

_STYLE = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 1100px;
       margin: 1.5rem auto; padding: 0 1rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
.subtitle { color: #666; margin-top: 0; }
.row { padding: 1rem; border: 1px solid #ddd; border-radius: 6px;
       margin-bottom: 0.8rem; background: #fafafa; }
.row.error { background: #fff0f0; border-color: #f0c0c0; }
.row.accepted { opacity: 0.6; }
.row.corrected { background: #f0f8ff; border-color: #c0d8f0; }
.filename { font-weight: 600; }
.meta { color: #666; font-size: 0.85rem; margin: 0.25rem 0 0.5rem; }
.classification { margin: 0.5rem 0; }
.confidence { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px;
              font-size: 0.85rem; }
.confidence.high   { background: #d4edda; color: #155724; }
.confidence.mid    { background: #fff3cd; color: #856404; }
.confidence.low    { background: #f8d7da; color: #721c24; }
.signals { font-size: 0.8rem; color: #555; margin-top: 0.4rem; }
.signals li { display: inline-block; margin-right: 0.6rem; }
.actions { margin-top: 0.6rem; }
.actions form { display: inline; margin-right: 0.4rem; }
.actions select, .actions input { padding: 0.2rem 0.4rem; font-size: 0.9rem; }
.actions button { padding: 0.3rem 0.7rem; font-size: 0.9rem; cursor: pointer; }
.status-tag { font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 3px;
              text-transform: uppercase; letter-spacing: 0.04em; }
.status-tag.pending   { background: #ddd; color: #444; }
.status-tag.accepted  { background: #d4edda; color: #155724; }
.status-tag.corrected { background: #cfe2ff; color: #0a4ba3; }
.status-tag.error     { background: #f8d7da; color: #721c24; }
.empty { color: #888; font-style: italic; }
.path-segment { color: #888; }
.path-segment strong { color: #222; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head>"
        f"<meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def _confidence_class(confidence: float) -> str:
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.4:
        return "mid"
    return "low"


def _signals_html(entry: LogEntry) -> str:
    if not entry.signals:
        return ""
    items = "".join(
        f"<li>{escape(s.detail)} (+{s.contribution:.2f})</li>"
        for s in entry.signals[:6]
    )
    more = "" if len(entry.signals) <= 6 else f" <em>+{len(entry.signals) - 6} more</em>"
    return f"<ul class='signals'>{items}</ul>{more}"


def _doc_type_options(doc_types: list[str], selected: str | None) -> str:
    options = ""
    for name in doc_types:
        sel = " selected" if selected and name == selected else ""
        options += f"<option value='{escape(name)}'{sel}>{escape(name)}</option>"
    return options


def _render_path(path: str | None) -> str:
    if not path:
        return "<span class='empty'>(not placed)</span>"
    return f"<code>{escape(path)}</code>"


def _render_row(entry: LogEntry, doc_types: list[str]) -> str:
    conf_pct = int(round(entry.confidence * 100))
    conf_class = _confidence_class(entry.confidence)
    status = entry.status

    classification_line = (
        f"<span class='confidence {conf_class}'>{conf_pct}%</span> "
        f"<strong>{escape(entry.doc_type or 'unclassified')}</strong>"
    )
    if entry.vendor:
        classification_line += f" <span class='path-segment'>· vendor: <strong>{escape(entry.vendor)}</strong></span>"

    if entry.error:
        actions = ""
    elif status == "pending":
        actions = f"""
        <div class='actions'>
          <form method='post' action='/classifications/{escape(entry.id)}/accept'>
            <button type='submit'>Accept</button>
          </form>
          <form method='post' action='/classifications/{escape(entry.id)}/correct'>
            <select name='doc_type' required>
              {_doc_type_options(doc_types, entry.doc_type)}
            </select>
            <input type='text' name='vendor' placeholder='vendor (optional)'
                   value="{escape(entry.vendor or '')}">
            <button type='submit'>Correct</button>
          </form>
        </div>
        """
    else:
        actions = ""

    return f"""
    <div class='row {status}'>
      <div class='filename'>{escape(entry.source_filename)}
        <span class='status-tag {status}'>{status}</span>
      </div>
      <div class='meta'>
        processed {escape(entry.processed_at.strftime('%Y-%m-%d %H:%M UTC'))}
      </div>
      <div class='classification'>{classification_line}</div>
      <div>placed at {_render_path(entry.final_path)}</div>
      {f"<div style='color:#a00'>error: {escape(entry.error)}</div>" if entry.error else ""}
      {_signals_html(entry)}
      {actions}
    </div>
    """


def render_inbox(entries: list[LogEntry], doc_types: list[str]) -> str:
    """Render the main inbox view."""
    if not entries:
        body = (
            "<h1>Inbox</h1>"
            "<p class='subtitle'>Drop a PDF in the watch folder. It will appear here once classified.</p>"
            "<p class='empty'>No documents processed yet.</p>"
        )
        return _page("Doc Sherpa — inbox", body)

    rows = "\n".join(_render_row(e, doc_types) for e in entries)
    body = f"""
    <h1>Inbox</h1>
    <p class='subtitle'>Most recent first. Accept what's right; correct what isn't.</p>
    {rows}
    """
    return _page("Doc Sherpa — inbox", body)
