# Unassigned Traffic Resolver

SmartHub read-only GA4 diagnostic for clients with a mapped Google Analytics property.

## SmartHub URL

`/tools/utm/unassigned-traffic/`

The resolver is exposed through the existing mounted UTM Builder because both tools manage attribution quality and this avoids another top-level WSGI mount.

## What it does

- Lists clients already mapped to Google Analytics in SmartHub's Google account index.
- Reuses the existing Google OAuth refresh token; no new Google connection is required.
- Measures total sessions and exact `Unassigned` sessions for 30, 60, 90, or 180 days.
- Pulls the top 250 Unassigned source / medium / campaign / landing-page combinations for diagnosis.
- Shows diagnostic coverage separately so a capped detail report can never understate the headline Unassigned total.
- Flags missing source/medium, non-standard media, self-referrals, payment referrals, paid traffic without campaign names, and likely Google Ads attribution issues.
- Suggests normalized UTM parameters for future campaign links.
- Does **not** rewrite historical GA4 attribution or change GA4/GTM configuration.

## Safety

All Google API operations are reads. Recommendations are presented for a person to review and implement. The resolver does not silently change a client's analytics property, Tag Manager container, referral settings, campaign URLs, or advertising account.

## Tests

`python3 test_unassigned_traffic.py`
