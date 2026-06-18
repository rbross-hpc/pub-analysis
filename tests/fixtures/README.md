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

### dorier-mofka.pdf

- Title  : Toward a persistent event-streaming system for high-performance computing applications
- Authors: Matthieu Dorier, Amal Gueroudji, Valérie Hayot-Sasson, et al. (ANL, U Chicago, ORNL, UW)
- Venue  : Frontiers in High Performance Computing (2025)
- DOI    : 10.3389/fhpcp.2025.1638203
- OSTI   : 3002321
- Source : OSTI accepted manuscript (https://www.osti.gov/servlets/purl/3002321)
- License: CC-BY 4.0 (Frontiers is fully open access; all content CC-BY by policy)
- sha256 : 8a4b26f9db6dc46f35860d5ac9a20c83140d6281777da4ec7eb3e371613c81a4
- Size   : 1.6 MB
- Why    : Multi-author ANL journal paper with DOI on page 1. Exercises bib
           resolution with a paper that has a real DOI in-text, multiple
           institutions, full-length body (42 pages), and the distillation
           pipeline (has a full abstract suitable for scope=abstract).

### cruz-zombie-packets.pdf

- Title  : Hybrid PDES Simulation of HPC Networks Using Zombie Packets
- Authors: Elkin Cruz-Camacho, Kevin A. Brown, Xin Wang, et al. (RPI, ANL, UIC, IIT)
- Venue  : ACM Transactions on Modeling and Computer Simulation (2025), vol. 35 no. 2
- DOI    : 10.1145/3682060
- OSTI   : 3017061
- Source : OSTI accepted manuscript (https://www.osti.gov/servlets/purl/3017061)
- License: Federally-funded author manuscript; redistributable under DOE public
           access policy (ASCR grant AC02-06CH11357). Publisher is ACM, but the
           OSTI deposit is the accepted manuscript, not the published version.
- sha256 : d3c17d2c43cb17551c8e17a39a481c03aca240c0de4c06ea701bf1a9edc91095
- Size   : 832 KB
- Why    : ACM journal article (different publisher than Frontiers fixtures). DOI
           is present on page 1, exercises the DOI-first resolution path. Also
           exercises the ACM TOMACS journal classification (no conference signal).
           PDF has run-together title glyph encoding that the LLM bootstrap
           resolves correctly.

### wan-e3smv2-clouds.pdf

- Title  : Features of mid- and high-latitude low-level clouds and their relation to strong aerosol effects in the Energy Exascale Earth System Model version 2 (E3SMv2)
- Authors: Hui Wan, Abhishek Yenpure, Berk Geveci, Richard C. Easter, Philip J. Rasch, Kai Zhang, Xubin Zeng (PNNL, Kitware, U Washington, U Arizona)
- Venue  : Geoscientific Model Development (2025), vol. 18 no. 17
- DOI    : 10.5194/gmd-18-5655-2025
- OSTI   : 2587778
- Source : OSTI accepted manuscript (https://www.osti.gov/servlets/purl/2587778)
- License: CC-BY 4.0 (Geoscientific Model Development is fully open access; all content CC-BY by policy)
- sha256 : 1532abf2563c075b35aaf7096e07585e34d835607d6aea08bd08ca13db7ef65a
- Size   : 9.2 MB
- Why    : Exercises OSTI author parsing when OSTI returns authors as a list of
           strings (e.g. "Wan, Hui [PNNL] (ORCID:...)") rather than dicts. Also
           exercises long-title resolution (120+ chars with parenthetical acronym)
           and DOI-based OSTI lookup for a multi-institution journal article.

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
