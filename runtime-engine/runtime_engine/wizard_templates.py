"""HTML for the setup wizard. Single page, three fields, save button."""

from __future__ import annotations

from html import escape
from pathlib import Path

from .wizard import WizardConfig

_STYLE = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 700px;
       margin: 3rem auto; padding: 0 1rem; color: #1a1a1a; }
h1 { font-size: 1.5rem; }
.subtitle { color: #666; margin-top: 0; }
label { display: block; margin-top: 1.2rem; font-weight: 600; }
.help { display: block; color: #666; font-size: 0.85rem; margin-top: 0.2rem;
        margin-bottom: 0.4rem; font-weight: normal; }
input[type=text] { width: 100%; padding: 0.5rem; font-size: 1rem; box-sizing: border-box; }
.pack-list { margin-top: 0.4rem; }
.pack-list label { display: inline-flex; align-items: center; font-weight: normal;
                   margin-right: 1rem; }
.pack-list input { margin-right: 0.3rem; }
button { margin-top: 1.5rem; padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
.problems { background: #fff0f0; border: 1px solid #f0c0c0; padding: 0.8rem;
            border-radius: 6px; margin-top: 1rem; color: #721c24; }
"""


def _checkbox(name: str, value: str, checked: bool, label: str) -> str:
    ch = " checked" if checked else ""
    return (
        f"<label><input type='checkbox' name='{escape(name)}' "
        f"value='{escape(value)}'{ch}>{escape(label)}</label>"
    )


def render_wizard(
    *,
    available_packs: list[str],
    current: WizardConfig | None,
    problems: list[str] | None = None,
) -> str:
    """Render the setup form. `current` pre-fills inputs when editing."""
    watch = escape(str(current.watch_dir)) if current else ""
    dest = escape(str(current.destination_root)) if current else ""
    active = set(current.active_packs) if current else set()
    auto = current.auto_create_folders if current else True

    packs_html = "".join(
        _checkbox("packs", pack, pack in active, pack) for pack in available_packs
    )

    problems_html = ""
    if problems:
        items = "".join(f"<li>{escape(p)}</li>" for p in problems)
        problems_html = f"<div class='problems'><strong>Setup problems:</strong><ul>{items}</ul></div>"

    body = f"""
    <h1>Doc Sherpa — first-run setup</h1>
    <p class='subtitle'>Two folders and a checkbox or two. You can change these later.</p>
    {problems_html}
    <form method='post' action='/setup'>
      <label>Watch folder
        <span class='help'>Where new scans arrive. Each PDF dropped here gets classified.</span>
        <input type='text' name='watch_dir' value='{watch}'
               placeholder='C:\\Scans\\Incoming' required>
      </label>

      <label>Destination root
        <span class='help'>Where classified PDFs are filed. Folders inside get created on demand.</span>
        <input type='text' name='destination_root' value='{dest}'
               placeholder='C:\\Scans\\Classified' required>
      </label>

      <label>Industry packs
        <span class='help'>Pick the document categories that match your business. You can change these later.</span>
        <div class='pack-list'>{packs_html}</div>
      </label>

      <label><input type='checkbox' name='auto_create_folders' value='1' {'checked' if auto else ''}>
        Auto-create folders as new doc types appear
      </label>

      <button type='submit'>Save and start watching</button>
    </form>
    """
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>Doc Sherpa — setup</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )
