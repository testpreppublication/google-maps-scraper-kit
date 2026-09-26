# School leadership & social enrichment

Use this optional second stage after `scripts/scrape.py` when the Maps leads are schools.

It prioritizes **Owner/Founder → Chairman → Managing Director → Director → Principal → Vice Principal**. It reads public leadership pages on the school's own website and records the source page. It does **not** log in to or scrape LinkedIn, Facebook, or Instagram.

For automatic public-web candidate discovery, set a Brave Search API key:

```bash
export BRAVE_SEARCH_API_KEY="..."
python scripts/enrich_school_leadership.py results.csv -o schools_enriched.csv
```

Without a search key, leadership discovery still works and the output includes ready-to-search LinkedIn/Facebook/Instagram queries.

## Added columns

`leadership_name, leadership_role, leadership_source, linkedin_person, facebook_person, instagram_person, profile_confidence, verification_status, *_search_query`

A social URL is only written when the public search evidence score is at least 60. Treat `candidate` as requiring human review; `verified_candidate` means stronger matching evidence, not identity proof.

Keep outreach compliant with applicable privacy/anti-spam rules and platform terms.
