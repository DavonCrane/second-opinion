"""Report export: markdown is canonical; optional styled PDF via headless Chromium/Edge if available.

PDF is a nice-to-have (demo polish). It never blocks a run: if no browser is found we simply skip it.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_CSS = """
body{font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;color:#1a2332;font-size:9.2pt;line-height:1.4;padding:.45in .55in;max-width:8.5in;margin:0 auto}
h1{font-size:15pt;color:#16324f;border-bottom:2.5px solid #16324f;padding-bottom:4px;margin:0 0 6px}
h2{font-size:8.8pt;text-transform:uppercase;letter-spacing:.8px;color:#16324f;border-bottom:1px solid #cdd5df;padding-bottom:2px;margin:9px 0 4px}
table{width:100%;border-collapse:collapse;margin:3px 0}th{font-size:7.2pt;text-transform:uppercase;color:#6b7686;text-align:left;padding:2px 5px;border-bottom:1px solid #cdd5df}
td{padding:2.3px 5px;border-bottom:.5px solid #e6ebf1;vertical-align:top}blockquote{background:#eef2f7;border:1px solid #cdd5df;border-radius:3px;padding:5px 9px;margin:5px 0}
ol,ul{margin:2px 0 2px 16px;padding:0}li{margin:0 0 2px}p{margin:2px 0}em{color:#5a6472}hr{border:0;border-top:1px solid #cdd5df;margin:8px 0}
.brand{text-align:center;border-bottom:2.5px solid #16324f;margin-bottom:8px;padding-bottom:4px;font-weight:700;color:#16324f;font-size:14pt;letter-spacing:.5px}
.brand i{display:block;font-weight:400;font-size:7.5pt;color:#6b7686;letter-spacing:0;margin-top:1px}
"""


def _md_to_html(md: str) -> str:
    """Small markdown subset renderer (headers, tables, bold/italic, lists, blockquotes, hr). No external deps."""
    out, in_table, in_list = [], False, None
    def inline(s: str) -> str:
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"_(.+?)_", r"<i>\1</i>", s)
        return s
    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>"); in_list = None
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not in_table:
                out.append("<table>"); in_table = True
                out.append("<tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        if not line:
            close_list(); continue
        if line.startswith("# "):
            close_list(); out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            close_list(); out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("---"):
            close_list(); out.append("<hr>")
        elif line.startswith("> "):
            close_list(); out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif re.match(r"^\d+\. ", line):
            if in_list != "ol":
                close_list(); out.append("<ol>"); in_list = "ol"
            item_text = re.sub(r"^\d+\. ", "", line)
            out.append(f"<li>{inline(item_text)}</li>")
        elif line.startswith(("- ", "* ")):
            if in_list != "ul":
                close_list(); out.append("<ul>"); in_list = "ul"
            out.append(f"<li>{inline(line[2:])}</li>")
        else:
            close_list(); out.append(f"<p>{inline(line)}</p>")
    if in_table:
        out.append("</table>")
    close_list()
    return "\n".join(out)


def to_html(md: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>"
            f"<div class='brand'>THE SECOND OPINION<i>The screener finds them. This does the homework.</i></div>"
            f"{_md_to_html(md)}</body></html>")


def _find_browser() -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              "/opt/pw-browsers/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if Path(p).exists():
            return p
    return None


def export_pdf(md: str, out_path: Path) -> Path | None:
    """Render markdown -> styled PDF using a headless Chromium-family browser. Returns None if unavailable."""
    browser = _find_browser()
    html_path = out_path.with_suffix(".html")
    html_path.write_text(to_html(md), encoding="utf-8")
    if not browser:
        return None
    try:
        subprocess.run([browser, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        f"--print-to-pdf={out_path}", html_path.as_uri()], check=True, capture_output=True, timeout=90)
        return out_path if out_path.exists() else None
    except Exception:  # noqa: BLE001
        return None
