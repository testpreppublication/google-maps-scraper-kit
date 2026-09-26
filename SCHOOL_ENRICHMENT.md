# School leadership & social enrichment

Use this school-specific pipeline after starting the local Maps scraper with Docker.

It prioritizes **Owner/Founder → Chairman → Managing Director → Director → Principal → Vice Principal**. It reads public leadership pages on the school's own website and records the source page. It does **not** log in to or scrape LinkedIn, Facebook, or Instagram.

## One-command workflow

```bash
docker compose up -d
python scripts/scrape_schools.py "CBSE schools in Lucknow" --city "Lucknow, Uttar Pradesh" --depth 8 --out lucknow-schools.xlsx
```

This produces a clean Excel workbook with Google Maps contact data, school-level social links, leadership details, person social-profile candidates, confidence, verification status, and source/search evidence.

For a CSV instead:

```bash
python scripts/scrape_schools.py "ICSE schools in Lucknow" --city "Lucknow, Uttar Pradesh" --out lucknow-icse.csv
```

## Automatic person-profile discovery

Set a Brave Search API key to automatically search the public web for leadership LinkedIn/Facebook/Instagram profile candidates:

```bash
export BRAVE_SEARCH_API_KEY="..."
python scripts/scrape_schools.py "CBSE schools in Lucknow" --city "Lucknow, Uttar Pradesh" --out lucknow-schools.xlsx
```

Without a search key, leadership discovery still works and the output includes ready-to-search LinkedIn/Facebook/Instagram queries.

## Output columns

Core school lead fields:

`title, category, address, phone, emails, website, review_rating, review_count`

Official school social links discovered from the school website:

`instagram, facebook, linkedin`

Leadership enrichment:

`leadership_name, leadership_role, leadership_source`

Preferred person profiles:

`linkedin_person, facebook_person, instagram_person`

Verification:

`profile_confidence, verification_status, *_search_query`

A person social URL is only written when the public search evidence score is at least 60. Treat `candidate` as requiring human review; `verified_candidate` means stronger matching evidence, not identity proof.

The XLSX writer uses only Python's standard library, freezes the header row, applies filtering, and sets practical column widths. No extra Python packages are required.

Keep outreach compliant with applicable privacy/anti-spam rules and platform terms.
