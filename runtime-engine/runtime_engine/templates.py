"""Inbox HTML — two-pane layout with drag-to-folder, alternatives, and signals.

Left pane: pending classifications. Right pane: destination folder tree.
Operators drag a pending row onto a folder to correct + file. Top-3
alternative doc types appear as clickable badges on each row.

All HTML inline; vanilla JS for the drag-drop, no framework. The CSS
is intentionally close to the previous single-pane version so the
project still feels like one piece, not three competing aesthetics.
"""

from __future__ import annotations

from html import escape

from .log import LogEntry

_STYLE = """
* { box-sizing: border-box; }
body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
       color: #1a1a1a; background: #f5f6f8; }
header { padding: 0.8rem 1.2rem; background: #fff; border-bottom: 1px solid #e0e3e7; }
header h1 { font-size: 1.1rem; margin: 0; }
header .subtitle { color: #666; font-size: 0.85rem; margin: 0.1rem 0 0; }
.layout { display: flex; min-height: calc(100vh - 60px); }
.inbox { flex: 1 1 auto; overflow-y: auto; padding: 1rem 1.5rem; }
.tree-pane { width: 320px; flex-shrink: 0; background: #fff; border-left: 1px solid #e0e3e7;
             padding: 1rem; overflow-y: auto; position: sticky; top: 60px; height: calc(100vh - 60px); }
.tree-pane h2 { font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.05em;
                color: #666; margin: 0 0 0.6rem; }

.row { padding: 0.9rem 1rem; border: 1px solid #ddd; border-radius: 6px;
       margin-bottom: 0.7rem; background: #fff; cursor: grab; }
.row.dragging { opacity: 0.4; }
.row.error { background: #fff0f0; border-color: #f0c0c0; }
.row.accepted { opacity: 0.5; cursor: default; }
.row.corrected { background: #f0f8ff; border-color: #c0d8f0; cursor: default; }

.filename { font-weight: 600; }
.meta { color: #888; font-size: 0.82rem; margin: 0.15rem 0 0.45rem; }
.classification { margin: 0.4rem 0; }
.confidence { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px;
              font-size: 0.82rem; }
.confidence.high { background: #d4edda; color: #155724; }
.confidence.mid  { background: #fff3cd; color: #856404; }
.confidence.low  { background: #f8d7da; color: #721c24; }

.alternatives { margin-top: 0.4rem; font-size: 0.85rem; color: #555; }
.alternatives .label { margin-right: 0.4rem; color: #888; }
.alt-pill { display: inline-block; padding: 0.1rem 0.55rem; margin-right: 0.3rem;
            border: 1px solid #c0c5cb; border-radius: 999px; background: #fafbfc;
            cursor: pointer; font-size: 0.8rem; }
.alt-pill:hover { background: #e8edf3; }
.alt-pill form { display: inline; }
.alt-pill button { background: none; border: 0; cursor: pointer; padding: 0;
                   font-size: 0.8rem; color: #333; }

.signals { font-size: 0.78rem; color: #555; margin-top: 0.4rem; }
.signals li { display: inline-block; margin-right: 0.6rem; }
.actions { margin-top: 0.55rem; }
.actions form { display: inline; margin-right: 0.4rem; }
.actions select, .actions input { padding: 0.25rem 0.45rem; font-size: 0.88rem; }
.actions button { padding: 0.28rem 0.7rem; font-size: 0.88rem; cursor: pointer; }
.status-tag { font-size: 0.7rem; padding: 0.08rem 0.4rem; border-radius: 3px;
              text-transform: uppercase; letter-spacing: 0.04em; }
.status-tag.pending   { background: #ddd; color: #444; }
.status-tag.accepted  { background: #d4edda; color: #155724; }
.status-tag.corrected { background: #cfe2ff; color: #0a4ba3; }
.status-tag.error     { background: #f8d7da; color: #721c24; }
.empty { color: #888; font-style: italic; }

.folder { display: block; padding: 0.35rem 0.6rem; border-radius: 4px; margin: 0.1rem 0;
          cursor: default; font-size: 0.9rem; user-select: none; }
.folder.over { background: #d3e4fb; outline: 2px dashed #5b8def; }
.folder.parent { font-weight: 600; }
.folder .children { margin-left: 1rem; font-weight: normal; }
.folder.empty-state { color: #aaa; font-style: italic; }
.toast { position: fixed; bottom: 1rem; right: 1rem; background: #333; color: #fff;
         padding: 0.6rem 1rem; border-radius: 6px; opacity: 0; transition: opacity .2s;
         font-size: 0.88rem; z-index: 10; }
.toast.visible { opacity: 1; }
"""

# Tiny drag/drop script. ~30 lines. No framework.
_DRAG_JS = """
(function() {
  function toast(msg) {
    let t = document.getElementById('toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'toast'; t.className = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add('visible');
    setTimeout(() => t.classList.remove('visible'), 1800);
  }

  document.querySelectorAll('.row[draggable=true]').forEach(row => {
    row.addEventListener('dragstart', (e) => {
      row.classList.add('dragging');
      e.dataTransfer.setData('text/plain', row.dataset.id);
      e.dataTransfer.effectAllowed = 'move';
    });
    row.addEventListener('dragend', () => row.classList.remove('dragging'));
  });

  document.querySelectorAll('.folder[data-rel]').forEach(folder => {
    folder.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      folder.classList.add('over');
    });
    folder.addEventListener('dragleave', () => folder.classList.remove('over'));
    folder.addEventListener('drop', async (e) => {
      e.preventDefault();
      folder.classList.remove('over');
      const id = e.dataTransfer.getData('text/plain');
      const rel = folder.dataset.rel;
      try {
        const r = await fetch(`/classifications/${id}/route-to`, {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({folder_rel: rel}),
        });
        if (r.ok) {
          toast(`Moved to ${rel}`);
          setTimeout(() => location.reload(), 600);
        } else {
          toast(`Move failed: ${r.status}`);
        }
      } catch (err) {
        toast(`Move failed: ${err}`);
      }
    });
  });
})();
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head>"
        f"<meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>{body}"
        f"<script>{_DRAG_JS}</script></body></html>"
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
        for s in entry.signals[:5]
    )
    more = "" if len(entry.signals) <= 5 else f" <em>+{len(entry.signals) - 5} more</em>"
    return f"<ul class='signals'>{items}</ul>{more}"


def _doc_type_options(doc_types: list[str], selected: str | None) -> str:
    options = ""
    for name in doc_types:
        sel = " selected" if selected and name == selected else ""
        options += f"<option value='{escape(name)}'{sel}>{escape(name)}</option>"
    return options


def _alternatives_html(entry: LogEntry) -> str:
    """Render top-3 alternative doc types as one-click correction pills."""
    if not entry.signals:  # alternatives are stored alongside; some legacy rows lack them
        pass
    # The classification result's alternatives are not stored in the log row
    # currently; we approximate by showing the doc types appearing in the
    # signals other than the chosen one. Cheap, useful enough.
    seen: set[str] = set()
    pills: list[str] = []
    for signal in entry.signals:
        if signal.kind != "keyword":
            continue
        # signal.detail looks like "bol: phrase 'BILL OF LADING'"
        prefix = signal.detail.split(":", 1)[0].strip()
        if prefix == entry.doc_type or prefix in seen:
            continue
        seen.add(prefix)
        pills.append(prefix)
        if len(pills) >= 3:
            break

    if not pills:
        return ""

    pill_html = "".join(
        f"<span class='alt-pill'>"
        f"<form method='post' action='/classifications/{escape(entry.id)}/correct' style='display:inline;'>"
        f"<input type='hidden' name='doc_type' value='{escape(p)}'>"
        f"<input type='hidden' name='vendor' value='{escape(entry.vendor or '')}'>"
        f"<button type='submit'>{escape(p)}</button>"
        f"</form></span>"
        for p in pills
    )
    return f"<div class='alternatives'><span class='label'>or:</span> {pill_html}</div>"


def _render_row(entry: LogEntry, doc_types: list[str]) -> str:
    conf_pct = int(round(entry.confidence * 100))
    conf_class = _confidence_class(entry.confidence)
    status = entry.status
    draggable = "true" if status == "pending" and not entry.error else "false"

    classification_line = (
        f"<span class='confidence {conf_class}'>{conf_pct}%</span> "
        f"<strong>{escape(entry.doc_type or 'unclassified')}</strong>"
    )
    if entry.vendor:
        classification_line += (
            f" <span class='meta' style='display:inline'>· vendor: "
            f"<strong>{escape(entry.vendor)}</strong></span>"
        )

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
            <input type='text' name='vendor' placeholder='vendor'
                   value="{escape(entry.vendor or '')}">
            <button type='submit'>Correct</button>
          </form>
        </div>
        """
    else:
        actions = ""

    return f"""
    <div class='row {status}' draggable='{draggable}' data-id='{escape(entry.id)}'>
      <div class='filename'>{escape(entry.source_filename)}
        <span class='status-tag {status}'>{status}</span>
      </div>
      <div class='meta'>
        processed {escape(entry.processed_at.strftime('%Y-%m-%d %H:%M UTC'))}
      </div>
      <div class='classification'>{classification_line}</div>
      <div class='meta'>at: <code>{escape(entry.final_path or '')}</code></div>
      {_alternatives_html(entry) if status == 'pending' else ''}
      {f"<div style='color:#a00; margin-top:.3rem'>error: {escape(entry.error)}</div>" if entry.error else ""}
      {_signals_html(entry)}
      {actions}
    </div>
    """


def _render_folder(folder: dict) -> str:
    """Render one folder + its children. Drop targets carry data-rel."""
    children_html = "".join(
        f"<span class='folder' data-rel='{escape(child['rel'])}'>{escape(child['name'])}</span>"
        for child in folder.get("children", [])
    )
    children_block = f"<div class='children'>{children_html}</div>" if children_html else ""
    return (
        f"<div class='folder parent' data-rel='{escape(folder['rel'])}'>"
        f"{escape(folder['name'])}"
        f"{children_block}"
        f"</div>"
    )


def render_inbox(
    entries: list[LogEntry],
    doc_types: list[str],
    folder_tree: list[dict],
) -> str:
    """Main page: inbox on the left, folder tree on the right."""
    if entries:
        inbox_html = "\n".join(_render_row(e, doc_types) for e in entries)
    else:
        inbox_html = (
            "<p class='empty'>No documents yet. Drop a PDF in the watch folder.</p>"
        )

    if folder_tree:
        tree_html = "\n".join(_render_folder(f) for f in folder_tree)
    else:
        tree_html = (
            "<p class='folder empty-state'>No folders yet — they'll appear here as "
            "documents are classified.</p>"
        )

    body = f"""
    <header>
      <h1>Doc Sherpa — inbox</h1>
      <p class='subtitle'>Drag a doc onto a folder to correct + file. Or use the buttons.</p>
    </header>
    <div class='layout'>
      <main class='inbox'>{inbox_html}</main>
      <aside class='tree-pane'>
        <h2>Destination folders</h2>
        {tree_html}
      </aside>
    </div>
    """
    return _page("Doc Sherpa — inbox", body)
