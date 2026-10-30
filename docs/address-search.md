# Geoapify address search

Status, October 8: owner selected Geoapify; frontend adapter and explicit-submit UI are implemented.
The owner-supplied key is configured in ignored local environment settings. A live browser request
for a public Craiova address returned HTTP 200; selecting the approximate result showed its precision
and nearby reference stations. Mobile selection had no horizontal overflow at 390 px. The GitHub Actions repository variable is configured for the public build. At that checkpoint domain
restrictions were not independently verified; see the October 30 readback below. The feature was published through PR #26.
Station coverage and modelled point estimates are separate unimplemented data products.

## Restriction readback — 30 October 2026

The authenticated Geoapify dashboard was independently inspected. The existing browser key has
three Allowed HTTP Origins: `https://ciuperceanusorinandrei.github.io`,
`http://127.0.0.1:5173`, and `http://localhost:5173`. Allowed IP addresses and Allowed HTTP Referers
have no filters. CORS response origin is `*`; CORS response configuration is separate from key
restrictions. No provider setting or key was changed in this session. Development and production
currently share this dedicated browser key; separate keys remain a follow-up, not a completed change.

A bounded provider probe using the existing local browser key and a public Craiova query returned
HTTP 200 with one result for each of the three configured origins; an unlisted HTTPS origin returned
HTTP 401. A request without an Origin header also returned HTTP 200. The observed restriction
therefore rejects an unlisted browser origin, but is not server authentication or proof that
non-browser clients cannot use the key. No key value or credential-bearing URL was logged or
included in the external review pack. Browser client keys are public by design.

The existing published UI was also exercised with `Strada Unirii 1, Craiova`; Geoapify returned selectable results labelled
with approximate precision, preserving the lower-confidence display. This checks the published adapter, not deployment
of the catalog changes.

Provider reference: [Geoapify API keys](https://myprojects.geoapify.com/help/api-keys/).



Validation: 31 frontend tests, ESLint and production build passed. Browser fixtures verified
explicit submission, empty results and quota-error retry; the live check verified a successful request.

## Local setup

1. Create a Geoapify account at https://myprojects.geoapify.com/ and a project for Atlasul Aerului.
2. Create a dedicated browser key. Restrict allowed origins/referrers to the actual local/production
   origins using Geoapify's supported key restrictions; do not use an unrestricted server key.
3. Add `VITE_GEOAPIFY_API_KEY=your_browser_key` to the existing ignored `.env.local`. Preserve its
   other entries; do not overwrite that file or put the key in a tracked file/chat.
4. Restart Vite and search a public test address by Enter or the search button. Choose a result,
   inspect its match precision and verify the map pin. Test empty, partial, timeout and quota results.

Vite embeds `VITE_` values in browser assets: this provider-specific client key is **public to users**,
not a secret. Domain restrictions, quota monitoring and a dedicated key are required operational
configuration. Database, RNMCA, CAMS and other private credentials remain server-side. If the
provider/account cannot enforce the needed browser restrictions, use a server endpoint instead.
No deployment credential has been created or configured by this local implementation.

## Behaviour

The client uses Geoapify forward geocoding, country-filtered to Romania, five results maximum,
Romanian labels, bounded 128 KB response reading and runtime validation. Calls occur only on
explicit submission, not typing. Editing/clearing/choosing a locality aborts the pending request;
late results cannot overwrite a newer query. A ten-second timeout and retry message preserve input.
API errors are generic and never echo request URLs/keys into application messages.

A building match is labelled at building level only with a house number, full match and provider
building confidence at least 0.9; this is a conservative local display rule, not a universal geocoder
accuracy guarantee. Street/locality/approximate matches retain their lower precision. The selected
location displays match precision and nearby reference stations. No arbitrary station radius, point
AQI or model estimate is invented. Geolocation retains the device-reported uncertainty.

Without a key, no external geocoding runs and local GeoNames/station search remains usable. Address
search controls are shown only when configured. The CSP allows only the explicit Geoapify API host
in addition to same-origin data; source attribution and external-query disclosure remain visible.

The public Nominatim endpoint is not integrated. Its policy forbids autocomplete and imposes
application-wide limits, developer responsibility and switchability; it is not a drop-in default.

## References

- https://apidocs.geoapify.com/docs/geocoding/
- https://apidocs.geoapify.com/docs/geocoding/address-autocomplete/
- https://operations.osmfoundation.org/policies/nominatim/
