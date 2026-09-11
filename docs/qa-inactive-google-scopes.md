# Inactive GA4 / GTM QA — Google Cloud scopes

The SmartHub QA cleanup tool needs these OAuth scopes in the Google Cloud project's OAuth consent screen:

- `https://www.googleapis.com/auth/analytics.edit`
- `https://www.googleapis.com/auth/tagmanager.delete.containers`

SmartHub requests these scopes for new or reconnected Google Finder logins. Existing refresh tokens keep their prior grants until the login is reconnected.

Official references:
- Google OAuth scopes: https://developers.google.com/identity/protocols/oauth2/scopes
- Tag Manager OAuth authorization: https://developers.google.com/tag-platform/tag-manager/api/v2/authorization
