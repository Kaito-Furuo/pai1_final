"""docs/report.md を HTML に変換し、Google Chrome（ヘッドレス）で PDF にする。

    .venv/bin/python scripts/build_report.py [--out docs/report.pdf]
"""
import argparse, os, subprocess, sys
import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser()
parser.add_argument("--src", default=os.path.join(ROOT, "docs", "report.md"))
parser.add_argument("--out", default=os.path.join(ROOT, "docs", "report.pdf"))
args = parser.parse_args()

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP", sans-serif; font-size: 10.5pt; line-height: 1.6; color: #222; }
h1 { font-size: 17pt; border-bottom: 2px solid #345; padding-bottom: 4px; }
h2 { font-size: 13.5pt; margin-top: 1.4em; border-left: 5px solid #3b6fd6; padding-left: 8px; }
h3 { font-size: 11.5pt; margin-top: 1.1em; }
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 9.5pt; }
th, td { border: 1px solid #bbb; padding: 4px 6px; vertical-align: top; }
th { background: #eef2f8; }
code { font-family: Menlo, monospace; font-size: 9pt; background: #f3f3f3; padding: 0 3px; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: 8px; font-size: 8.8pt; white-space: pre-wrap; }
img { max-width: 100%; }
hr { border: 0; border-top: 1px solid #ccc; }
.fig { text-align: center; margin: 0.8em 0; }
.fig img { max-height: 70mm; margin: 0 4px; }
"""
md = open(args.src, encoding="utf-8").read()
html = markdown.markdown(md, extensions=["tables", "fenced_code"])
page = f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><style>{CSS}</style></head><body>{html}</body></html>"
html_path = os.path.splitext(args.out)[0] + ".html"
open(html_path, "w", encoding="utf-8").write(page)
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if not os.path.exists(chrome):
    chrome = "google-chrome"
cmd = [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={args.out}", "file://" + os.path.abspath(html_path)]
subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(f"wrote {args.out} ({os.path.getsize(args.out)//1024} KB) and {html_path}")
