# YouTube Studio rollout

Entry: `/tools/youtube/`, Client 360 → Social & links → YouTube accounts,
and Google Access → YouTube account access.

## Phase 1 — Accounts and owner access

Link channel IDs, handles or channel URLs to a client. Search returns candidate
channels for a staff member to check. Existing YouTube social links in Client 360
open the tool with both the client and channel prefilled. Creating an owner
access link requires the existing Google OAuth configuration and a valid
TOKEN_ENCRYPTION_KEY. Links last seven days, are scoped to a client and optionally
a channel, and are replaced when regenerated. Connecting consumes the link.

The Google Access callback `/connect/callback` dispatches only `yt_` OAuth states
to YouTube. These states are time limited, single use, tied to the initiating
browser session, and use PKCE. The token endpoint must grant all requested scopes.
`channels.list(mine=true)` must return the intended channel. Credentials are
encrypted at rest and excluded from every staff/customer payload. Connecting a
channel already managed under another client is refused.

## Phase 2 — Review and launch preparation

Refresh reads up to 25 recent videos and supplies a limited, evidence-based
metadata review. It does not claim to judge thumbnails or retention. Launch
planning saves an About draft, playlist suggestions, four weekly video briefs and
a launch checklist. AI copy suggestions use the existing Hub AI service and
client brief; output is editable and never automatically applied. Existing-video
updates verify channel ownership and preserve tags, category and language.
Thumbnail and caption uploads and private playlist creation are supported.

## Phase 3 — Drafts, customer review and publishing

Drafts are client/channel scoped. Editing resets approval. Customer links expose
one draft's metadata and disclosure fields; they are not a full client portal or
a proof of the finished video. Stale revision approvals are refused. Staff may
also approve details. Only approved drafts can upload; files are streamed from
the Hub to a resumable YouTube upload session with private visibility. Maximum
Hub upload size is 256 MB. Source files are not retained by this module.

Upload state is claimed under the shared cross-worker file lock. Uncertain
uploads are not automatically repeated. Check upload status can recover a
completed upload using its encrypted session URL. Incomplete or expired sessions
require checking YouTube Studio before starting a replacement draft. Publish
checks processing and ownership; future schedules must be at least ten minutes
ahead and include a timezone. UI times are converted from local time to UTC.

## Phase 4 — Results and team queue

Owner-authorized daily views, watch minutes, average view duration and subscriber
changes are available for a 28-day window ending two days ago. No website leads
or attributed revenue are inferred. The team queue lists stored connection gaps,
review findings and unfinished drafts. This release reads results on demand;
it does not send customer messages or run recurring reports.

## Configuration and live validation

- Enable YouTube Data API v3 and YouTube Analytics API on the Google OAuth project.
- Existing `GOOGLE_ACCESS_CLIENT_ID`/`GOOGLE_ACCESS_CLIENT_SECRET` are used, falling
  back to `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, like Google Access.
- Existing `/connect/callback` authorized redirect URI is reused.
- `TOKEN_ENCRYPTION_KEY` is required; credentials never fall back to plaintext.
- `YOUTUBE_API_KEY` enables public search/lookup, with `GOOGLE_API_KEY` as fallback.
- Google consent-screen scopes and production verification may require the
  Google project administrator. Owner consent cannot be completed by code.
- YouTube restricts uploads from unaudited API projects to private visibility.
  Confirm audit status before offering automatic public publishing.

Offline tests cover authentication, CSRF, wrong-channel consent, expired/replaced
links, replay, cross-client access, approval revisions, private upload,
duplicate-upload prevention, uncertain upload and safe metadata updates. Real
owner consent and a real upload require an authorized customer account and media.

## Further production capabilities

The content package currently produces written Shorts concepts, social copy,
a blog outline and thumbnail briefs. It links to existing production tools;
it does not yet automatically render clips, deliver them to Social Planner,
attribute GA4 leads, generate paid proposals, provide customer logins/roles,
or send monthly reports. Channel creation and permission invitations remain
guided steps in YouTube. These are further phases, not claimed as shipped.

API references: https://developers.google.com/youtube/v3/docs/videos/insert,
https://developers.google.com/youtube/v3/docs/channels,
https://support.google.com/youtube/answer/9481328.
