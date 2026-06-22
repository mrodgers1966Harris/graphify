# Atlas Bid Book — Normalized Schema Spec v1.0

Source-of-truth data model. BuildingConnected (BC) is an INPUT only; this is the app schema.

Hierarchy: **Project → CSI Division → Bid Package → Bidder → Proposal → {Leveling, Risk, Award}**

## Per-project file shape

Each project is emitted as ONE JSON object (file: `project_<slug>.json`) matching this exactly:

```json
{
  "id": "<bc project id>",
  "name": "string",
  "number": "string|null",
  "value": 0,
  "sqft": 0,
  "location": "string|null",
  "client": "string|null",
  "architect": "string|null",
  "state": "PUBLISHED|DRAFT",
  "phase": "pre-bid|bidding|leveling|awarding|post-award",
  "status": "green|yellow|red",
  "bidsDueAt": "YYYY-MM-DD|null",
  "rfisDueAt": "YYYY-MM-DD|null",
  "startsAt": "YYYY-MM-DD|null",
  "endsAt": "YYYY-MM-DD|null",
  "notes": "string|null",
  "rollup": {
    "divisions": 0, "packages": 0, "packagesPublished": 0,
    "invitedBidders": 0, "bidsReceived": 0, "coveragePct": 0,
    "packagesWithNoBid": 0, "packagesSingleBid": 0, "awarded": 0,
    "lowBidTotal": 0
  },
  "divisions": [
    {
      "csi": "22",
      "title": "Plumbing",
      "rollup": { "packages": 0, "bidsReceived": 0, "invitedBidders": 0,
                  "packagesWithNoBid": 0, "highestRisk": "none|low|medium|high" },
      "packages": [
        {
          "id": "<bc bid package id>",
          "name": "string",
          "csiNumber": "220000",        // raw BC number, may be ""
          "csiDivision": "22",          // first 2 digits; "00" if unknown/blank
          "keywords": ["..."],
          "state": "PUBLISHED|DRAFT",
          "estimatedCost": 0,
          "bidsDueAt": "YYYY-MM-DD|null",
          "award": {
            "awardedCompanyId": "string|null",
            "awardedCompanyName": "string|null",
            "awardedBidId": "string|null",
            "awardedLeveledTotal": 0,
            "recommendedCompanyId": "string|null",
            "recommendedCompanyName": "string|null",
            "recommendedBidId": "string|null",
            "recommendedLeveledTotal": 0,
            "recommendationBasis": "low leveled bid|single bid (no competition)|no bids received|already awarded",
            "needsReview": true
          },
          "leveling": {
            "inviteCount": 0,
            "bidCount": 0,
            "coverage": "0/0",
            "low": 0, "high": 0, "avg": 0, "median": 0,
            "spread": 0,            // high-low
            "spreadPct": 0,         // (high-low)/low, 0 if low==0
            "outlierBidIds": []     // bids > 1.5x median or < 0.5x median
          },
          "risk": [
            { "type": "no_bids|single_bid|wide_spread|low_coverage|long_lead|past_due_no_award|draft_unpublished",
              "severity": "low|medium|high",
              "note": "human-readable" }
          ],
          "bidders": [
            {
              "companyId": "string",
              "companyName": "string",
              "inviteState": "INVITED|VIEWED|BIDDING|BID_SUBMITTED|NOT_BIDDING|UNDECIDED|null",
              "contacts": [ { "name": "First Last", "title": "string", "email": "string", "phone": "string" } ],
              "bid": {
                "bidId": "string",
                "total": 0,
                "leveledTotal": 0,
                "submittedAt": "YYYY-MM-DD|null",
                "notes": "string",
                "lineItems": [ { "description": "string", "value": 0, "unit": "string" } ],
                "attachments": [ { "attachmentId": "string", "bidId": "string" } ],
                "isLow": false,
                "deltaFromLow": 0
              }
            }
          ]
        }
      ]
    }
  ]
}
```

## Rules

**CSI division** = first 2 chars of `csiNumber`. If number is "" / null / non-numeric → `csiDivision="00"`, title "Unassigned / General". Use the standard MasterFormat division titles (01 General Requirements, 02 Existing Conditions, 03 Concrete, 04 Masonry, 05 Metals, 06 Wood/Plastics/Composites, 07 Thermal & Moisture, 08 Openings, 09 Finishes, 10 Specialties, 11 Equipment, 12 Furnishings, 13 Special Construction, 14 Conveying, 21 Fire Suppression, 22 Plumbing, 23 HVAC, 26 Electrical, 27 Communications, 28 Electronic Safety/Security, 31 Earthwork, 32 Exterior Improvements, 33 Utilities). Sort divisions ascending by csi.

**Bidders**: union of invites (list_invites) and bids (list_bids), keyed by companyId. Company name comes from invite `bidderCompany.name` (trim). A bidder with an invite but no matching bid → `bid: null`. A bid whose companyId has no invite still gets a row (name from any available source else "Unknown").

**Leveling** (only over bidders WITH a bid): low/high/avg/median of `leveledTotal` (fallback `total`). `isLow` on the min. `deltaFromLow = leveledTotal - low`. outlier if >1.5×median or <0.5×median (only when bidCount≥3).

**Award recommendation**: if `awardedCompany` set on package → use it, basis "already awarded", needsReview=false. Else if bidCount==0 → recommended null, basis "no bids received", needsReview=true. Else if bidCount==1 → that bidder, basis "single bid (no competition)", needsReview=true. Else → lowest leveledTotal, basis "low leveled bid", needsReview = (spreadPct>0.5).

**Risk auto-derivation** (add all that apply):
- `no_bids` HIGH — package PUBLISHED, 0 bids.
- `single_bid` MEDIUM — exactly 1 bid.
- `wide_spread` MEDIUM — bidCount≥3 and spreadPct>0.5.
- `low_coverage` LOW — bidCount/inviteCount < 0.25 and inviteCount≥4.
- `long_lead` MEDIUM — name/keywords match: elevator, escalator, switchgear, gear, generator, structural steel, steel joist, curtain wall, glazing, storefront, HVAC equipment, chiller, AHU, transformer, millwork.
- `past_due_no_award` MEDIUM — bidsDueAt in the past (relative 2026-06-03) and no award.
- `draft_unpublished` LOW — package state DRAFT.

**phase** (project): if all packages DRAFT → pre-bid; if bidsDueAt in future → bidding; if bidsDueAt past and no awards → leveling; if some awards → awarding; if all awarded → post-award.

**status**: red if any HIGH risk on a package due within 7 days OR bidsDueAt past with 0 bids on key packages; yellow if upcoming deadline or open mediums; green otherwise. Keep existing manual notes.

Money: numbers (no $ or commas). Dates: ISO `YYYY-MM-DD`. Never invent values — use null/0 when BC has none.
