"""One page per provider under /reports/provider-check: the field map each
pull expects, what the platform documents that the pull does not read, and
what has to be true on Render for the pull to run at all.

``/reports/provider-check`` is the overview -- every platform's raw table
against ``provider_map.py`` -- and the four live check pages (AudioGo,
GroundTruth, Amazon DSP, CallRail) call an endpoint and print what came back. What
neither answered was the question a person asks with a key in hand:
*which fields does the Hub read from this platform, which does the
platform offer that the Hub leaves on the table, and what do I set on the
service?* This module answers it for every platform, and the answer is
drawn from the code rather than written beside it: the field map is read
off the module that does the pull (``audiogo_map.config()``,
``amazon_dsp.FIELD_MAP``, ``stackadapt.QUERY``, ``google_ads_perf.gaql()``,
the CSV alias tables), so a correction there is a correction here.

## Two kinds of "not read"

* **Answered and not read** is measured. Where a live check page has an
  answer, ``unread_from_answer()`` is the row keys the endpoint returned
  minus the names the map reads; where a Windsor raw table is present,
  ``unread_table_columns()`` is the table's columns minus the ones the map
  names. Both are facts about this deployment.
* **Documented and not read** is a transcription. ``DOCUMENTED`` carries,
  per platform, the field names the platform's own reference or help
  pages list that no reader here asks for, each block naming its source.
  A name in it is what the document says, not what an endpoint answered,
  and the page labels it so. AudioGo's and GroundTruth's lists are short
  because their references sit behind hosts the Hub's environment cannot
  reach (``docs/claude/53``); what is here is what the public pages and
  the search index show of them, and no more.

## What Render has to carry

``ENV`` is one row per variable a pull reads, with what stays broken while
it is unset. It is worded as *what has to be true*, never as what is
missing: nothing here can read the service's environment or the env group
linked to it (CLAUDE.md, the Render note), so a page that said "unset"
would be guessing. The pull's own ``missing()`` is the measurement, and the
page prints it beside this list where the module has one.
"""
from __future__ import annotations

from . import provider_map, store

# The order the submenu lists them in: the native pulls first, in the order
# the nightly job runs them, then the platforms that arrive only through
# the managed provider's tables.
NATIVE = ("ttd", "google", "bing", "stackadapt", "audiogo", "groundtruth", "amazon_dsp", "callrail")
WINDSOR_ONLY = tuple(p for p in store.PLATFORMS if p not in NATIVE and p != "suite")
ORDER = NATIVE + WINDSOR_ONLY

# The live check pages that exist. A platform not here has no endpoint the
# Hub calls on a page load; its page says so rather than pretending.
CHECK_PAGES = {
    "audiogo": "/reports/audiogo-check",
    "groundtruth": "/reports/groundtruth-check",
    "amazon_dsp": "/reports/amazon-check",
    "callrail": "/reports/callrail-check",
}
# What the link to each is called, as the buttons that used to sit on every
# Reports screen called them.
CHECK_LABELS = {"audiogo": "AudioGo check", "groundtruth": "GroundTruth check",
                "amazon_dsp": "Amazon check", "callrail": "CallRail check"}


def _env(name: str, unset: str, *, required: bool = True, source: str = "") -> dict:
    return {"name": name, "unset": unset, "required": required, "source": source}


# What has to be true on the service, per native pull. ``required`` rows
# are the ones the pull's missing() refuses without; the rest are
# overrides and pins. The wording is "what unset costs", the shape
# Settings.status() uses on /status.
ENV: dict[str, list[dict]] = {
    "ttd": [
        _env("TTD_API_TOKEN", "the pull refuses by name; a long-lived token from Preferences > "
             "Developer Portal, sent as the TTD-Auth header. TRADE_DESK_API is read as a twin."),
        _env("TTD_PARTNER_ID", "the pull refuses by name; every advertiser hangs off it."),
        _env("TTD_REPORT_TEMPLATE_ID", "the first run cannot create the MyReports schedule and "
             "says so; once the schedule exists it is found by name.", required=False),
        _env("TTD_API_BASE", "defaults to https://api.thetradedesk.com/v3.", required=False),
        _env("TTD_REPORT_DATE_RANGE", "defaults to LastThirtyDays.", required=False),
    ],
    "google": [
        _env("GOOGLE_ADS_CLIENT_ID", "not connected; Connect Google Ads on /tools/ads/settings cannot start."),
        _env("GOOGLE_ADS_CLIENT_SECRET", "not connected."),
        _env("GOOGLE_ADS_DEVELOPER_TOKEN", "not connected; the API refuses every query."),
        _env("GOOGLE_ADS_REDIRECT_URI", "the consent round-trip cannot land."),
        _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "the manager account is not expanded to its clients.",
             required=False),
        _env("GOOGLE_ADS_REFRESH_TOKEN", "the consent lives in the database instead; set to pin it "
             "across a redeploy.", required=False),
    ],
    "bing": [
        _env("BING_AD_CLIENT_ID", "not connected; Connect Microsoft Ads on /tools/ads/settings cannot start."),
        _env("BING_AD_CLIENT_SECRET", "not connected."),
        _env("BING_AD_DEVELOPER_TOKEN", "not connected; Reporting v13 refuses every submit."),
        _env("BING_MANAGER_ACCOUNT_ID", "no accounts are enumerated under the manager."),
        _env("BING_MANAGER_ACCOUNT_NUMBER", "the manager's account number is not carried on the "
             "report request.", required=False),
        _env("BING_AD_REFRESH_TOKEN", "the consent lives in the database instead; set to pin it "
             "across a redeploy.", required=False),
    ],
    "stackadapt": [
        _env("STACKADAPT_API_KEY", "the pull refuses by name; sent as the Authorization header to the "
             "GraphQL endpoint. STACK_ADAPT_API is read as a twin."),
        _env("STACKADAPT_API_ENDPOINT", "defaults to https://api.stackadapt.com/graphql.", required=False),
        _env("STACKADAPT_AUTH_HEADER", "defaults to Authorization.", required=False),
    ],
    "audiogo": [
        _env("AUDIOGO_API_KEY", "the pull refuses by name. AudioGo's own FAQ says the key is sent as "
             "the x-api-key header, which is now the default; AUDIO_GO_API is read as a twin, so the "
             "spelling Render already carries answers.",
             source="audiogo.com/faqs/api-reporting"),
        _env("AUDIOGO_API_BASE", "the pull refuses by name. The origin is in the Reporting API spec "
             "(a PDF downloaded from audiogo.com/how-to/audiogo-reporting-api) and on no public page "
             "the Hub's environment can read; paste it from the spec.",
             source="audiogo.com/how-to/audiogo-reporting-api"),
        _env("AUDIOGO_REPORT_PATH", "the placeholder path is called. The spec names the report "
             "endpoint; the FAQ says separate Dimensions and Metrics endpoints list what a report "
             "may ask for.", required=False, source="audiogo.com/how-to/dimensions-metrics-api"),
        _env("AUDIOGO_ROWS_PATH", "rows are looked for under `data`.", required=False),
        _env("AUDIOGO_FIELD_IMPRESSIONS", "the map reads `impressions`. The FAQ names the "
             "impressions metric `demandAudioImp`; set this to it once the check page shows that "
             "key on a row.", required=False, source="audiogo.com/how-to/dimensions-metrics-api"),
        _env("AUDIOGO_AUTH_HEADER", "defaults to x-api-key (AudioGo's documented header); only "
             "set if the spec says otherwise.", required=False),
    ],
    "groundtruth": [
        _env("GROUND_TRUTH_API", "the pull refuses by name. Set on Render under this spelling "
             "(docs/claude/53)."),
        _env("GROUND_TRUTH_API_BASE", "nothing is called and the key is sent nowhere. The public "
             "API's own examples call https://api-public.groundtruth.com; setting it to that origin "
             "is what lets the check page make its first call.",
             source="api-docs.groundtruth.com"),
        _env("GROUND_TRUTH_REPORT_PATH", "the placeholder path is called. The reference lists "
             "campaign, ad group and creative timeseries endpoints by day, day of week and time of "
             "day; the day-series campaign path is the one this pull wants.",
             required=False, source="api-docs.groundtruth.com"),
        _env("GROUND_TRUTH_AUTH_HEADER", "defaults to Authorization with a Bearer prefix, which is a "
             "placeholder: the Welcome page names the real header and the sandbox cannot read it. "
             "GROUND_TRUTH_AUTH_PREFIX beside it.", required=False),
        _env("GROUND_TRUTH_MONTHLY_LIMIT", "calls are counted and read as not measured against a "
             "limit.", required=False),
    ],
    "amazon_dsp": [
        _env("AMAZON_ADS_CLIENT_ID", "not configured; nothing is called."),
        _env("AMAZON_ADS_CLIENT_SECRET", "not configured; nothing is called."),
        _env("AMAZON_DSP_ENTITY_ID", "not configured; the entity's profile cannot be looked for."),
        _env("AMAZON_ADS_REGION", "defaults to NA.", required=False),
        _env("AMAZON_DSP_ENTITY_PROFILE_ID", "profile discovery runs on every pull.", required=False),
        _env("AMAZON_ADS_REFRESH_TOKEN", "the consent lives in the database instead; set to pin it "
             "across a redeploy.", required=False),
    ],
    "callrail": [
        _env("CALLRAIL_API_KEY", "the pull refuses by name. An API key from a CallRail user's "
             "settings (Account > API Keys); the reference sends it as Authorization: Token "
             "token=\"<key>\".", source="apidocs.callrail.com"),
        _env("CALLRAIL_API_BASE", "nothing is called and the key is sent nowhere. The reference's "
             "own examples call https://api.callrail.com; setting it to that origin is what lets "
             "the check page make its first call.", source="apidocs.callrail.com"),
        _env("CALLRAIL_ACCOUNT_ID", "every account the key sees is read; set to read one account "
             "and skip the accounts call.", required=False),
        _env("CALLRAIL_CALLS_PATH", "the placeholder path /v3/a/{account_id}/calls.json is called; "
             "CALLRAIL_ACCOUNTS_PATH beside it for /v3/a.json.", required=False,
             source="apidocs.callrail.com"),
        _env("CALLRAIL_REQUEST_FIELDS", "the request asks for company_id, company_name, source, "
             "first_call and lead_status beyond the default fields.", required=False),
        _env("CALLRAIL_AUTH_FORMAT", "defaults to Token token=\"{key}\" on the Authorization "
             "header, the reference's shape; CALLRAIL_AUTH_HEADER beside it.", required=False),
        _env("CALLRAIL_MONTHLY_LIMIT", "calls are counted and read as not measured against a "
             "limit.", required=False),
    ],
}

# How the key reaches the platform, in a sentence, and where the reference
# is. ``verified`` says whether a live answer has confirmed the shape.
HOW: dict[str, dict] = {
    "ttd": {"line": "Platform API v3 with a long-lived token in the TTD-Auth header; the figures "
                    "come from a MyReports schedule's CSV, not an ad-hoc stats call.",
            "docs": "partner.thetradedesk.com/v3/portal/api/ref", "verified": False},
    "google": {"line": "Google Ads API, one GAQL query per client account under the manager, through "
                       "Smart 1 Ads' OAuth consent and developer token.",
               "docs": "developers.google.com/google-ads/api/fields/latest/campaign", "verified": True},
    "bing": {"line": "Microsoft Advertising Reporting API v13: a daily CampaignPerformanceReportRequest "
                     "submitted, polled and read back as one CSV in a ZIP.",
             "docs": "learn.microsoft.com/advertising/reporting-service/campaignperformancereportcolumn",
             "verified": True},
    "stackadapt": {"line": "GraphQL campaignDelivery, granularity DAILY, paged on records; the key in "
                           "the Authorization header.",
                   "docs": "the SDK's generated schema (dist/gql/graphql.d.ts)", "verified": False},
    "audiogo": {"line": "Reporting API with the key in an x-api-key header (AudioGo's FAQ). Agency "
                        "customers request the key from AudioGo Support; the spec is a PDF, and the "
                        "Dimensions and Metrics endpoints list what a report may ask for. Synchronous "
                        "reports take up to three dimensions.",
                "docs": "audiogo.com/how-to/audiogo-reporting-api", "verified": False},
    "groundtruth": {"line": "Public API at api-public.groundtruth.com, JSON, with campaign, ad group "
                            "and creative timeseries endpoints by day, day of week and time of day. "
                            "Metrics update daily; today's spend every two hours. The auth header is "
                            "on the Welcome page, which the Hub's environment cannot read.",
                    "docs": "api-docs.groundtruth.com", "verified": False},
    "amazon_dsp": {"line": "Amazon Ads API: Login with Amazon consent, the DSP entity's profile, and an "
                           "asynchronous daily ORDER + LINE_ITEM report per advertiser.",
                   "docs": "advertising.amazon.com/API/docs (DSP reports)", "verified": False},
    "callrail": {"line": "CallRail API v3, JSON, with the key in an Authorization: Token header. The "
                         "accounts the key sees, then every call in the window per account, paged "
                         "250 at a time, counted here by company, source and day. Not media: a row "
                         "is calls, never spend. The reference is on a host the Hub's environment "
                         "cannot reach, so the shape is a transcription.",
                 "docs": "apidocs.callrail.com", "verified": False},
}

# Documented by the platform and not read by any pull here. Each block is
# a transcription from the source it names, never a live answer; the page
# says so above the box. The ``why`` explains the choice where one was
# made, so the next person does not add a field the module refused on
# purpose.
DOCUMENTED: dict[str, dict] = {
    "ttd": {
        "source": "The Trade Desk MyReports standard performance template, as the exports on the "
                  "ad-ops inbox and the parser's alias list describe it",
        "fields": [
            "TTD Cost", "Data Cost", "Media Cost", "Partner Cost (USD), read only as a fallback for Advertiser Cost",
            "Bids", "Win Rate", "CTR", "CPM", "CPC", "eCPA",
            "Player 25% Views", "Player 50% Views", "Player 75% Views", "Player Muted", "Player Unmuted",
            "Viewable Impressions", "Measurable Impressions", "Ad Group ID", "Ad Group", "Creative ID",
            "Creative", "Device Type", "Site", "Supply Vendor", "Ad Format",
        ],
        "why": "Rates (CTR, CPM, CPC, eCPA) are computed from the counts the fact table already holds. "
               "Cost splits, quartiles and viewability have no fact column and no tile yet; ad group, "
               "creative, device and site are a finer grain than the campaign-day the table is keyed on.",
    },
    "google": {
        "source": "Google Ads API campaign resource, metrics and segments reference",
        "fields": [
            "metrics.all_conversions", "metrics.conversions_value", "metrics.all_conversions_value",
            "metrics.view_through_conversions", "metrics.cost_per_conversion", "metrics.ctr",
            "metrics.average_cpc", "metrics.average_cpm", "metrics.interactions", "metrics.engagements",
            "metrics.video_views", "metrics.video_quartile_p25_rate", "metrics.video_quartile_p50_rate",
            "metrics.video_quartile_p75_rate", "metrics.search_impression_share",
            "metrics.search_budget_lost_impression_share", "metrics.search_rank_lost_impression_share",
            "metrics.absolute_top_impression_percentage", "metrics.phone_calls",
            "campaign.status", "campaign.bidding_strategy_type", "campaign_budget.amount_micros",
            "segments.device", "segments.ad_network_type",
        ],
        "why": "The query asks for the five fact columns, the channel type and the p100 rate, and "
               "nothing else: every added metric is a field a client could be shown, and the client "
               "page draws only what a tile explains. Budgets come from the Hub's own budget lines, "
               "not the platform's.",
    },
    "bing": {
        "source": "Microsoft Advertising Reporting API v13, CampaignPerformanceReportColumn",
        "fields": [
            "Ctr", "AverageCpc", "AverageCpm", "ConversionRate", "CostPerConversion", "Revenue",
            "ReturnOnAdSpend", "AllConversions", "AllRevenue", "Assists", "ViewThroughConversions",
            "PhoneCalls", "PhoneImpressions", "ImpressionSharePercent", "ImpressionLostToBudgetPercent",
            "ImpressionLostToRankAggPercent", "QualityScore", "TopImpressionRatePercent",
            "AbsoluteTopImpressionRatePercent", "VideoViews", "VideoViewsAt25Percent",
            "VideoViewsAt50Percent", "VideoViewsAt75Percent", "CompletedVideoViews", "DeviceType",
            "Network", "AdDistribution", "BudgetName", "BudgetStatus",
        ],
        "why": "CampaignType is requested and read (it files the default product); CampaignStatus, "
               "AccountNumber and CurrencyCode are requested and carried on the row's extras or not "
               "read at all -- the box below the map says which. Video columns wait on a Microsoft "
               "video buy that has a tile to land on.",
    },
    "stackadapt": {
        "source": "the SDK's generated schema for DeliveryStatsRecord, as the module docstring cites "
                  "it; transcribed, and no live key has answered yet",
        "fields": [
            "ctr", "cpm", "cpc", "cpa", "conversionRevenue", "uniqueImpressions", "videoFirstQuartile",
            "videoMidpoint", "videoThirdQuartile", "engagements",
            "lineItem { id name } (a finer grain than the campaign-day)",
            "creative { id name } (a finer grain than the campaign-day)",
        ],
        "why": "The query is one constant, read against the schema; the four audio and video counts "
               "it asks for beyond the fact columns all land in extras. Rates are computed here; "
               "quartiles and per-creative rows have nowhere to go yet.",
    },
    "audiogo": {
        "source": "audiogo.com/how-to/dimensions-metrics-api and /faqs/api-reporting -- what the "
                  "public pages say; the spec PDF has not been read",
        "fields": [
            "demandAudioImp (the impressions metric, by the FAQ's own example)",
            "geoCity (listener's city)", "playerName (the player rendering the ad)",
            "device OS, city, campaign and advertiser as report filters",
            "the Dimensions endpoint's full list", "the Metrics endpoint's full list",
        ],
        "why": "Every field name in audiogo_map.py is a placeholder until the check page shows a row. "
               "demandAudioImp is the one documented metric name the search index surfaced, and it "
               "is what AUDIOGO_FIELD_IMPRESSIONS should be set to once a row carries it.",
    },
    "groundtruth": {
        "source": "api-docs.groundtruth.com's endpoint list and help.groundtruth.com's reporting "
                  "articles, as the search index shows them; no page has been read whole",
        "fields": [
            "cost per visit", "observed visits vs projected visits", "secondary actions and secondary action rate (click to call, click for directions)",
            "ad group timeseries", "creative timeseries", "day-of-week series", "time-of-day series",
            "geographic breakdowns (state, DMA, county, zip, drive-to-store)", "DOOH ad groups",
            "campaign and ad group settings (Get Campaigns, Get Adgroups)",
        ],
        "why": "Visits ride in extras and draw the Store visits tile; secondary actions are the nearest "
               "thing to a conversion and stay unmapped until the document says which field, because "
               "a guess files somebody's store visits as conversions (docs/claude/53).",
    },
    "amazon_dsp": {
        "source": "Amazon Ads API DSP reports reference, the CAMPAIGN report's metric list; "
                  "transcribed, and CONFIRMED in amazon_dsp.py is still False",
        "fields": [
            "totalFee", "supplyCost", "amazonAudienceFee", "amazonPlatformFee", "CTR", "eCPM", "eCPC",
            "viewableImpressions", "measurableImpressions", "viewabilityRate", "videoStart",
            "videoFirstQuartile", "videoMidpoint", "videoThirdQuartile", "totalAddToCart",
            "totalNewToBrandPurchases", "totalSales", "totalUnitsSold", "totalPixel",
            "CREATIVE, SITE and SUPPLY_SOURCE as report dimensions",
        ],
        "why": "totalCost is the advertiser-currency figure the client is billed against; fee splits "
               "would double-count beside it. Purchases and detail-page views ride in extras and are "
               "never folded into conversions, which this platform does not report.",
    },
    "callrail": {
        "source": "apidocs.callrail.com's API v3 reference for calls, as the search index shows it; "
                  "no page has been read whole",
        "fields": [
            "customer_name, customer_phone_number, customer_city, customer_state, customer_country",
            "tracking_phone_number, business_phone_number, tracker_id",
            "recording, recording_duration, recording_player, transcription, keywords_spotted, call_highlights",
            "medium, campaign, keywords, referring_url, landing_page_url, referrer_domain",
            "utm_source, utm_medium, utm_campaign, utm_term, utm_content, gclid, fbclid, msclkid",
            "device_type, value, tags, note, agent_email, prior_calls, total_calls",
            "calls/summary.json and calls/timeseries.json (totals grouped by one key)",
            "form_submissions.json (web forms a tracker captured) and text_messages.json",
        ],
        "why": "A row is a count per company, source and day, so anything about one caller -- who they "
               "are, where they called from, what was said -- is neither asked for nor kept; the check "
               "page masks it. Attribution finer than the source (medium, campaign, keyword, UTMs) "
               "waits on a screen that would draw it. Form submissions and texts are other outcomes "
               "with no tile yet.",
    },
}


# ---------------------------------------------------------------------------
# The field map each pull expects, read off the module that does the pull
# ---------------------------------------------------------------------------

def _rows(pairs, *, note: str = "") -> list[dict]:
    return [{"fact": k, "field": v, "note": note} for k, v in pairs]


def field_map(platform: str) -> dict:
    """``{"rows": [{fact, field, note}], "source": str, "placeholder": bool,
    "requested_unread": [names]}`` -- the map as the pull reads it today.
    ``requested_unread`` is the names a request asks the platform for and
    then does not read, which is its own kind of waste."""
    if platform == "audiogo":
        from . import audiogo_map
        c = audiogo_map.config()
        return {"rows": _rows(c["fields"].items()), "source": "modules/reports/audiogo_map.py",
                "placeholder": bool(c.get("placeholder")), "requested_unread": []}
    if platform == "groundtruth":
        from . import groundtruth_map
        c = groundtruth_map.config()
        return {"rows": _rows(c["fields"].items()), "source": "modules/reports/groundtruth_map.py",
                "placeholder": bool(c.get("placeholder")), "requested_unread": []}
    if platform == "callrail":
        from . import callrail_map
        c = callrail_map.config()
        return {"rows": _rows(c["fields"].items()), "source": "modules/reports/callrail_map.py",
                "placeholder": bool(c.get("placeholder")), "requested_unread": []}
    if platform == "amazon_dsp":
        from . import amazon_dsp
        try:
            from modules.ads_builder import amazon_ads as amz
            asked = list(amz.REPORT_METRICS)
        except Exception:                               # noqa: BLE001 - standalone
            asked = []
        read = set(amazon_dsp.FIELD_MAP.values())
        return {"rows": _rows(amazon_dsp.FIELD_MAP.items()),
                "source": "modules/reports/amazon_dsp.py FIELD_MAP",
                "placeholder": not amazon_dsp.CONFIRMED,
                "requested_unread": [m for m in asked if m not in read]}
    if platform == "stackadapt":
        pairs = [("date", "granularity.startTime"), ("account_id", "campaign.advertiser.id"),
                 ("account_name", "campaign.advertiser.name"), ("campaign_id", "campaign.id"),
                 ("campaign_name", "campaign.name"), ("spend", "metrics.cost"),
                 ("impressions", "metrics.impressions"), ("clicks", "metrics.clicks"),
                 ("conversions", "metrics.conversions"), ("video_views", "metrics.videoStarts"),
                 ("completes", "metrics.videoCompletions + metrics.audioCompletions"),
                 ("extras.audioStarts", "metrics.audioStarts")]
        return {"rows": _rows(pairs), "source": "modules/reports/stackadapt.py QUERY",
                "placeholder": True, "requested_unread": []}
    if platform == "google":
        pairs = [("date", "segments.date"), ("account_id", "the client customer id"),
                 ("campaign_id", "campaign.id"), ("campaign_name", "campaign.name"),
                 ("spend", "metrics.cost_micros / 1,000,000"), ("impressions", "metrics.impressions"),
                 ("clicks", "metrics.clicks"), ("conversions", "metrics.conversions"),
                 ("video_views", "metrics.video_trueview_views"),
                 ("completes", "metrics.video_quartile_p100_rate x impressions, video campaigns only"),
                 ("extras.channel_type", "campaign.advertising_channel_type")]
        return {"rows": _rows(pairs), "source": "modules/reports/google_ads_perf.py gaql()",
                "placeholder": False, "requested_unread": []}
    if platform == "bing":
        from . import bing
        try:
            from modules.ads_builder import bing_ads as ba
            asked = list(ba.CAMPAIGN_COLUMNS)
        except Exception:                               # noqa: BLE001
            asked = []
        rows = _rows((k, " | ".join(v)) for k, v in bing.ALIASES.items())
        read = {bing._norm(n) for names in bing.ALIASES.values() for n in names}
        return {"rows": rows, "source": "modules/reports/bing.py ALIASES (CSV header spellings)",
                "placeholder": False,
                "requested_unread": [c for c in asked if bing._norm(c) not in read]}
    if platform == "ttd":
        from .parsers import ttd_myreports
        rows = _rows((k, " | ".join(v)) for k, v in ttd_myreports.ALIASES.items())
        return {"rows": rows, "source": "modules/reports/parsers/ttd_myreports.py ALIASES "
                                        "(MyReports CSV header spellings)",
                "placeholder": False, "requested_unread": []}
    # A platform with no native pull: what Windsor's table is expected to
    # carry is the whole of its map.
    src = provider_map.PLATFORM_SOURCES.get(platform)
    if not src:
        return {"rows": [], "source": "", "placeholder": True, "requested_unread": []}
    pairs = [(f, src[f]) for f in provider_map.REQUIRED_FIELDS]
    pairs.append(("conversions", src.get("conversions")))
    pairs.extend((f"extras.{e}", e) for e in (src.get("extras") or []))
    return {"rows": _rows(pairs), "source": "modules/reports/provider_map.py (Windsor's raw table)",
            "placeholder": True, "requested_unread": []}


def windsor_map(platform: str) -> dict | None:
    """The provider-table half every platform has: the raw table
    ``provider_map.py`` expects and its columns."""
    src = provider_map.PLATFORM_SOURCES.get(platform)
    if not src:
        return None
    return {"table": src["table"], "columns": provider_map.required_columns(src),
            "spend_divisor": src["spend_divisor"], "restate_days": src["restate_days"]}


def unread_table_columns(platform: str, tables: dict | None = None) -> dict:
    """Windsor's table, measured: ``{"present": bool, "unread": [cols]}`` --
    the columns on the raw table the map does not name. Nothing when the
    table is not there yet, and said so."""
    src = provider_map.PLATFORM_SOURCES.get(platform)
    if not src:
        return {"present": False, "unread": []}
    if tables is None:
        try:
            from . import normalize
            tables = normalize.schema_tables()
        except Exception:                               # noqa: BLE001 - the page must render
            tables = {}
    cols = tables.get(src["table"])
    if cols is None:
        return {"present": False, "unread": []}
    read = set(provider_map.required_columns(src))
    return {"present": True, "unread": [c for c in cols if c not in read]}


def _mapped_names(platform: str) -> set:
    """Every response name the map reads, including the first segment of a
    dotted path, so ``campaign.id`` counts ``campaign`` as read."""
    out = set()
    for row in field_map(platform)["rows"]:
        for name in str(row["field"] or "").split(" | "):
            name = name.strip()
            if name:
                out.add(name)
                out.add(name.split(".")[0])
    return out


def unread_from_answer(platform: str, row_keys) -> list[str]:
    """Answered and not read: the keys a live check page saw on a row that
    the map does not name. Measured, and only where a row came back."""
    read = _mapped_names(platform)
    return [k for k in (row_keys or []) if k not in read]


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def nav(active: str = "") -> list[dict]:
    """The submenu: the overview, then one entry per platform, the native
    pulls first. ``active`` marks the current page."""
    items = [{"key": "", "label": "Overview", "href": "/reports/provider-check", "on": active == ""}]
    for p in ORDER:
        items.append({"key": p, "label": store.platform_label(p),
                      "href": f"/reports/provider-check/{p}", "on": active == p,
                      "native": p in NATIVE})
    return items


def _status(platform: str) -> dict:
    """The pull's own status line and missing() -- the measurement beside
    the list of what has to be true. {} for a platform with no pull."""
    mod = {"ttd": "ttd", "google": "google_ads_perf", "bing": "bing", "stackadapt": "stackadapt",
           "audiogo": "audiogo", "groundtruth": "groundtruth", "amazon_dsp": "amazon_dsp",
           "callrail": "callrail"}.get(platform)
    if not mod:
        return {}
    try:
        import importlib
        m = importlib.import_module(f"modules.reports.{mod}")
        return m.status()
    except Exception as exc:                            # noqa: BLE001 - a status is not the page
        return {"connected": False, "missing": [],
                "line": f"{store.platform_label(platform)}: status could not be read ({type(exc).__name__})"}


def page(platform: str, tables: dict | None = None) -> dict | None:
    """Everything the provider page renders, or None for a platform the
    module does not know."""
    if platform not in ORDER:
        return None
    return {
        "platform": platform,
        "label": store.platform_label(platform),
        "native": platform in NATIVE,
        "check_page": CHECK_PAGES.get(platform),
        "how": HOW.get(platform),
        "env": ENV.get(platform, []),
        "status": _status(platform),
        "map": field_map(platform),
        "windsor": windsor_map(platform),
        "table": unread_table_columns(platform, tables),
        "documented": DOCUMENTED.get(platform),
        "nav": nav(platform),
    }
