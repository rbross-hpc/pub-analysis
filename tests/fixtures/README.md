# Test fixtures

PDFs in this directory are used by puba's end-to-end tests. All fixtures must be
redistributable. Add new fixtures only after verifying license terms.

## Inclusion criteria

- Redistributable license (CC-BY, public domain, or equivalent)
- Small enough to keep clones reasonably fast (target < ~10 MB total)
- Stable bibliographic identifiers (DOI / OSTI ID / arXiv ID)
- Exercises a distinct PDF formatting tradition or classification path

## Current fixtures

### klasky-5.pdf

- Title  : Scalable foundation models for numerical simulations on HPC platforms
- Authors: Dali Wang, Qian Gong, Zirui Liu, Xiao Wang, Qinglei Cao, Scott Klasky
- Venue  : Frontiers in High Performance Computing (2026)
- DOI    : 10.3389/fhpcp.2026.1778471
- OSTI   : 3028571
- License: CC-BY 4.0 (Frontiers is fully open access; all content CC-BY by policy)
- sha256 : c48fee04e8b0c9ae1136e5056a7ae31df804b2291f073b8738a5082fe1adfdb1
- Size   : 128 KB
- Why    : Smallest available real-paper fixture. Exercises journal-article
           classification, DOI-only resolution, and tier-1 agreement
           (OpenAlex + CrossRef + OSTI all expected to return the same record).

### zfp-spectral-report.pdf

- Title  : Supporting Special Values in ZFP
- Author : Peter Lindstrom (LLNL)
- Venue  : OSTI white paper / technical report (no journal)
- DOI    : 10.2172/2998448  (10.2172 is OSTI's own DOI minter for deposited reports)
- OSTI   : 2998448
- License: Public domain — DOE-authored OSTI technical report; work of the
           U.S. Government, not subject to copyright per 17 U.S.C. § 105
- sha256 : f9dc04eece444efa8a4accd789aa1ce1041a67b0af7b0f636dce88b520a35a19
- Size   : 7.3 MB
- Why    : Exercises the 10.2172/ DOI prefix (OSTI-minted), "technical report"
           classification, OSTI-as-canonical-source path, and the no-venue
           handling fallback. Complements klasky-5 which has a full journal venue.

## Verifying integrity

```bash
sha256sum tests/fixtures/*.pdf
```

## Adding new fixtures

1. Verify the license is redistributable (CC-BY, public domain, or confirmed
   federally-funded author manuscript on OSTI).
2. Prefer PDFs < 2 MB. If larger, document why size is justified.
3. Add an entry to this README with title, authors, venue, DOI/OSTI, license,
   sha256, size, and a brief "Why" note.
4. Add an assertion in tests/test_e2e_bib.py covering the new fixture.
