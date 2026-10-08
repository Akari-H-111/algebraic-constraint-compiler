"""Build the static GitHub Pages demo: the simulated display plus the Python engine for Pyodide.

    python3 tools/site/build_site.py <out_dir>
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from algebraic_compiler.simulator import SCENARIOS  # noqa: E402

ENGINE = ["__init__.py", "ir.py", "linear_ir.py", "linear.py", "linear_verifier.py", "linear_view.py",
          "service.py", "notebook.py", "web/card.html"]
BANNER = ('<div class="static-banner"><b>Web demo</b> · Guided requests run the real Python compiler and independent '
          'verifier <b>in your browser</b> (Pyodide). No server, no AI. <span id="engine-status">Loading…</span> '
          '<span class="more">· To ask in your own words with a Claude/Gemini agent, or to use the MCP server: '
          '<a href="https://github.com/Akari-H-111/algebraic-constraint-compiler">run it locally</a>.</span></div>')
SITE = "https://akari-h-111.github.io/algebraic-constraint-compiler/"
DESCRIPTION = ("Check a child's math homework in your browser. The real Python compiler and an independent verifier replay "
               "an exact proof. No server, no AI. A simulated Alexa+ display, not an Amazon product.")
# Shared-link preview, and an early connection to the CDN that serves the Python runtime (the largest download).
HEAD = (f'<meta property="og:type" content="website">\n<meta property="og:title" content="Show Your Work: math answers Alexa+ can prove">\n'
        f'<meta property="og:description" content="{DESCRIPTION}">\n<meta property="og:url" content="{SITE}">\n'
        f'<meta property="og:image" content="{SITE}social-preview.png">\n<meta property="og:image:width" content="1280">\n'
        f'<meta property="og:image:height" content="640">\n'
        f'<meta property="og:image:alt" content="Show Your Work: a simulated Alexa+ display showing the exact line where a homework answer changes, with a verified certificate">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>')


def main():
    out = Path(sys.argv[1])
    shutil.rmtree(out, ignore_errors=True)
    (out / "engine/algebraic_compiler/web").mkdir(parents=True)
    web = ROOT / "algebraic_compiler/web/alexa"
    html = (web / "index.html").read_text()
    html = html.replace('href="/alexa.css"', 'href="alexa.css"').replace(
        '<script src="/alexa.js" defer></script>', '<script src="static-backend.js"></script>\n<script src="alexa.js" defer></script>')
    html = html.replace("<title>Show Your Work · Simulated Alexa+</title>", "<title>Show Your Work · Web demo</title>\n" + HEAD)
    html = html.replace('content="Check a child\'s math homework and see an exact proof an independent verifier replayed. '
                        'A simulated Alexa+ display; not an Amazon product."', f'content="{DESCRIPTION}"')
    assert f'<meta name="description" content="{DESCRIPTION}">' in html, "page description was not replaced"
    html = html.replace("<body>", "<body>\n" + BANNER, 1)
    (out / "index.html").write_text(html)
    css = (web / "alexa.css").read_text() + (
        "\n.static-banner{font-size:13px;color:#cfd7ea;background:rgba(39,211,255,.08);border-bottom:1px solid rgba(39,211,255,.25);"
        "padding:8px 24px}.static-banner a{color:#27d3ff}#engine-status{color:#96a1b8}\n"
        "@media (max-width:560px){.static-banner{padding:8px 12px}.static-banner .more{display:none}}\n")
    (out / "alexa.css").write_text(css)
    shutil.copy2(web / "alexa.js", out / "alexa.js")
    shutil.copytree(web / "voice", out / "voice")  # recorded Gemini TTS clips and their manifest
    shutil.copy2(ROOT / "docs/images/social-preview.png", out / "social-preview.png")  # the page's og:image (1280x640)
    shutil.copy2(ROOT / "tools/site/static-backend.js", out / "static-backend.js")
    for name in ENGINE:
        shutil.copy2(ROOT / "algebraic_compiler" / name, out / "engine/algebraic_compiler" / name)
    (out / "engine/manifest.json").write_text(json.dumps({"files": [f"algebraic_compiler/{n}" for n in ENGINE]}, indent=1))
    (out / "scenarios.json").write_text(json.dumps(SCENARIOS, indent=1))
    (out / ".nojekyll").write_text("")
    print(f"built {out}")


if __name__ == "__main__":
    main()
