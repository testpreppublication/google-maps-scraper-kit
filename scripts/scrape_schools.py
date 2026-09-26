#!/usr/bin/env python3
"""One-command school lead pipeline.

Maps -> school contact details -> official socials -> leadership -> public person-profile candidates
-> clean CSV or XLSX.

Examples:
  python scripts/scrape_schools.py "CBSE schools in Lucknow" --city "Lucknow, Uttar Pradesh" --depth 8
  python scripts/scrape_schools.py "ICSE schools in Kanpur" --city "Kanpur, Uttar Pradesh" --out kanpur-schools.xlsx

Set BRAVE_SEARCH_API_KEY for automatic LinkedIn/Facebook/Instagram person-candidate discovery.
Without it, leadership discovery still runs and search-query columns are generated.
"""

import argparse
import csv
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
SCRAPE = ROOT / "scripts" / "scrape.py"
ENRICH = ROOT / "scripts" / "enrich_school_leadership.py"

PREFERRED = [
    "title", "category", "address", "phone", "emails", "website",
    "review_rating", "review_count",
    "instagram", "facebook", "linkedin",
    "leadership_name", "leadership_role", "leadership_source",
    "linkedin_person", "facebook_person", "instagram_person",
    "profile_confidence", "verification_status",
    "linkedin_search_query", "facebook_search_query", "instagram_search_query",
]

def col_letter(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out

def xlsx_from_csv(csv_path, xlsx_path):
    """Write a simple Excel workbook using only the Python standard library."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        rows = [["No results"]]

    def cell(ref, value, style=0):
        value = "" if value is None else str(value)
        return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t>{escape(value)}</t></is></c>'

    sheet_rows = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, value in enumerate(row, 1):
            cells.append(cell(f"{col_letter(ci)}{ri}", value, 1 if ri == 1 else 0))
        sheet_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')

    widths = []
    if rows:
        for ci in range(len(rows[0])):
            max_len = max((len(str(r[ci])) if ci < len(r) else 0) for r in rows[:500])
            widths.append(min(max(max_len + 2, 10), 45))
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths, 1)
    )

    last_col = col_letter(max(1, len(rows[0])))
    last_row = len(rows)
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>{cols}</cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  <autoFilter ref="A1:{last_col}{last_row}"/>
</worksheet>'''

    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="School Leads" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
 <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
 <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
 <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
 <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>
 <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
 <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

    with zipfile.ZipFile(xlsx_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)

def reorder_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    seen = set()
    fields = []
    for k in PREFERRED:
        if any(k in r for r in rows):
            fields.append(k); seen.add(k)
    for r in rows:
        for k in r:
            if k not in seen:
                fields.append(k); seen.add(k)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

def main():
    ap = argparse.ArgumentParser(description="One-command school lead + leadership enrichment pipeline.")
    ap.add_argument("query", help='e.g. "CBSE schools in Lucknow"')
    ap.add_argument("--city", required=True, help='e.g. "Lucknow, Uttar Pradesh"')
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--out", default="school-leads.xlsx", help="final .xlsx or .csv path")
    ap.add_argument("--no-email", action="store_true", help="skip email extraction")
    ap.add_argument("--delay", type=float, default=0.5, help="delay between leadership-enrichment website checks")
    args = ap.parse_args()

    out = Path(args.out)
    if out.suffix.lower() not in {".csv", ".xlsx"}:
        out = out.with_suffix(".xlsx")

    with tempfile.TemporaryDirectory(prefix="school-leads-") as td:
        maps_csv = Path(td) / "maps.csv"
        enriched_csv = Path(td) / "enriched.csv"

        cmd = [
            sys.executable, str(SCRAPE), args.query,
            "--city", args.city,
            "--depth", str(args.depth),
            "--out", str(maps_csv),
            "--socials",
            "--no-email",
        ]

        print("\n=== Stage 1/2: Google Maps school leads + official school socials (fast Maps mode) ===", flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)

        print("\n=== Stage 2/2: parallel website enrichment (leadership + emails + person social-profile candidates) ===", flush=True)
        enrich_cmd = [
            sys.executable, str(ENRICH), str(maps_csv),
            "-o", str(enriched_csv), "--delay", str(args.delay), "--workers", "8"
        ]
        if args.no_email:
            enrich_cmd.append("--skip-email")
        subprocess.run(enrich_cmd, cwd=ROOT, check=True)

        reorder_csv(enriched_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".xlsx":
            xlsx_from_csv(enriched_csv, out)
        else:
            out.write_bytes(enriched_csv.read_bytes())

    print(f"\n✓ Final school database: {out}")
    if not os.getenv("BRAVE_SEARCH_API_KEY"):
        print("ℹ BRAVE_SEARCH_API_KEY is not set: leadership names are still discovered,")
        print("  but person LinkedIn/Facebook/Instagram URLs may be blank; ready-to-search queries are included.")

if __name__ == "__main__":
    main()
