/* =============================================================================
   Stadium to Screen — market data + deterministic audience model  (v3)
   -----------------------------------------------------------------------------
   WHAT CHANGED IN v3 (and why)
   -----------------------------------------------------------------------------
   The old model produced numbers that contradicted each other on screen:

     households  940,000     <- every home in the DMA
     fanBase     470,000     <- PEOPLE, so it read as "fewer fans than houses"
     matchable 3,825,800     <- devices for the ENTIRE market, not for fans
                                => 4x the households and 8x the "fan base"

   The bug: devices were computed from ALL households in the market instead of
   from the FAN households we can actually reach. So the funnel got WIDER as it
   got more specific, which is nonsense to a customer.

   v3 is a strict funnel. Every step narrows, in one unit (HOMES), and only the
   final step changes unit (to SCREENS) with an explicit "about N screens per
   home" explanation:

     homesInMarket   ->  fanHomes  ->  reachableHomes  ->  screens
        940,000          188,000        161,680          ~658,000
                         (20% of        (86% of          (~4.1 screens
                          homes)         fan homes)       per reached home)

   Pricing tiers are unchanged: `fanBase` (people) is still exported and still
   lands in the same scenario bracket as before, so lib/pdf.js pricing is stable.

   NOTE: public/index.html must NOT inline its own copy of this model any more.
   It reads window.S1_DATA. One source of truth.

   >>> VERIFY BEFORE LAUNCH <<<
   - DMA_HOUSEHOLDS are approximate Nielsen TV-household counts.
   - STATE_POP are approximate Census populations.
   - Every multiplier below is an editable planning assumption in ONE place.
============================================================================= */

(function (root) {

  // ---- Editable planning assumptions (document these to clients) -------------
  const AVG_HH_SIZE = 2.5;               // people per household (US avg)

  const DEVICE_PER_HH = {                // connected devices per household (avg)
    phones: 2.6,
    computers: 1.6,
    tablets: 0.9,
    connectedTV: 2.3
  };
  const MATCH_RATE = 0.55;               // share of devices realistically matchable programmatically

  // Share of HOUSEHOLDS in the geography that are reachable fan households.
  // (v2 applied this to PEOPLE, which is what made fanBase < households.)
  const FAN_HH_PENETRATION = {
    local: 0.20,                         // dense, home-market concentration
    regional: 0.08,                      // statewide
    national: 0.03                       // nationwide
  };

  // Of the fan households, the share we can actually address programmatically
  // (has at least one matchable connected device / resolvable household IP).
  const ADDRESSABLE_RATE = 0.86;

  // Football fans living in a fan household. Used ONLY for the "people" stat
  // and for pricing-tier selection — never shown next to a household count.
  const FANS_PER_FAN_HH = 2.2;

  const NATIONAL = { households: 132000000, population: 335000000 };

  // ---- Baseline geography data (APPROX — verify) -----------------------------
  const DMA_HOUSEHOLDS = {
    "New York, NY": 7450000, "Los Angeles, CA": 5700000, "Chicago, IL": 3500000,
    "Philadelphia, PA": 3050000, "Dallas-Fort Worth, TX": 2950000, "Houston, TX": 2700000,
    "San Francisco-Oakland-San Jose, CA": 2700000, "Washington, DC": 2600000,
    "Atlanta, GA": 2650000, "Boston, MA": 2500000, "Phoenix, AZ": 2150000,
    "Tampa-St. Petersburg, FL": 2050000, "Seattle-Tacoma, WA": 2050000,
    "Detroit, MI": 1900000, "Minneapolis-St. Paul, MN": 1850000, "Denver, CO": 1750000,
    "Miami-Fort Lauderdale, FL": 1750000, "Cleveland-Akron, OH": 1480000,
    "Charlotte, NC": 1320000, "Raleigh-Durham, NC": 1300000, "Nashville, TN": 1270000,
    "Baltimore, MD": 1180000, "Salt Lake City, UT": 1180000, "Indianapolis, IN": 1160000,
    "Pittsburgh, PA": 1130000, "Columbus, OH": 1000000, "Kansas City, MO": 970000,
    "Austin, TX": 950000, "Cincinnati, OH": 940000, "Las Vegas, NV": 900000,
    "Greenville-Spartanburg-Asheville-Anderson": 900000, "Jacksonville, FL": 800000,
    "Oklahoma City, OK": 750000, "Birmingham, AL": 760000, "Louisville, KY": 700000,
    "New Orleans, LA": 690000, "Memphis, TN": 690000, "Buffalo, NY": 630000,
    "Knoxville, TN": 570000, "Tulsa, OK": 540000, "Lexington, KY": 500000,
    "Madison, WI": 470000, "Roanoke-Lynchburg, VA": 450000, "Green Bay, WI": 440000,
    "Columbia, SC": 420000, "Lincoln & Hastings-Kearney, NE": 400000,
    "Waco-Temple-Bryan, TX": 380000, "Cedar Rapids-Waterloo-Iowa City, IA": 350000,
    "South Bend-Elkhart, IN": 320000, "Fort Smith-Fayetteville-Springdale-Rogers, AR": 300000,
    "Tallahassee-Thomasville, FL": 280000, "Johnstown-Altoona-State College, PA": 280000,
    "Eugene, OR": 270000, "Lansing, MI": 250000, "Columbus-Tupelo-West Point, MS": 200000,
    "Columbia-Jefferson City, MO": 200000, "Topeka, KS": 170000, "Lubbock, TX": 160000,
    "Gainesville, FL": 130000, "Clarksburg-Weston, WV": 120000,
    "Columbus, GA (Opelika-Auburn)": 210000
  };

  const STATES = {
    AL:["Alabama",5100000], AK:["Alaska",733000], AZ:["Arizona",7400000], AR:["Arkansas",3070000],
    CA:["California",38900000], CO:["Colorado",5900000], CT:["Connecticut",3600000], DE:["Delaware",1030000],
    DC:["District of Columbia",680000], FL:["Florida",22600000], GA:["Georgia",11000000], HI:["Hawaii",1440000],
    ID:["Idaho",1960000], IL:["Illinois",12500000], IN:["Indiana",6860000], IA:["Iowa",3200000],
    KS:["Kansas",2940000], KY:["Kentucky",4530000], LA:["Louisiana",4570000], ME:["Maine",1390000],
    MD:["Maryland",6180000], MA:["Massachusetts",7000000], MI:["Michigan",10040000], MN:["Minnesota",5740000],
    MS:["Mississippi",2940000], MO:["Missouri",6190000], MT:["Montana",1130000], NE:["Nebraska",1980000],
    NV:["Nevada",3190000], NH:["New Hampshire",1400000], NJ:["New Jersey",9290000], NM:["New Mexico",2110000],
    NY:["New York",19570000], NC:["North Carolina",10700000], ND:["North Dakota",780000], OH:["Ohio",11790000],
    OK:["Oklahoma",4050000], OR:["Oregon",4240000], PA:["Pennsylvania",12960000], RI:["Rhode Island",1100000],
    SC:["South Carolina",5370000], SD:["South Dakota",920000], TN:["Tennessee",7130000], TX:["Texas",30500000],
    UT:["Utah",3420000], VT:["Vermont",650000], VA:["Virginia",8720000], WA:["Washington",7810000],
    WV:["West Virginia",1770000], WI:["Wisconsin",5910000], WY:["Wyoming",580000]
  };

  // ---- Team rosters ----------------------------------------------------------
  // {name, city, venue, state, dma, rsn (regional sports net), nick}
  const COLLEGE = [
    ["Ohio State Buckeyes","Columbus, OH","Ohio Stadium","OH","Columbus, OH","Big Ten Network"],
    ["Michigan Wolverines","Ann Arbor, MI","Michigan Stadium","MI","Detroit, MI","Big Ten Network"],
    ["Alabama Crimson Tide","Tuscaloosa, AL","Bryant-Denny Stadium","AL","Birmingham, AL","SEC Network"],
    ["Georgia Bulldogs","Athens, GA","Sanford Stadium","GA","Atlanta, GA","SEC Network"],
    ["Texas Longhorns","Austin, TX","DKR–Texas Memorial Stadium","TX","Austin, TX","SEC Network"],
    ["Penn State Nittany Lions","State College, PA","Beaver Stadium","PA","Johnstown-Altoona-State College, PA","Big Ten Network"],
    ["LSU Tigers","Baton Rouge, LA","Tiger Stadium","LA","New Orleans, LA","SEC Network"],
    ["Tennessee Volunteers","Knoxville, TN","Neyland Stadium","TN","Knoxville, TN","SEC Network"],
    ["Notre Dame Fighting Irish","South Bend, IN","Notre Dame Stadium","IN","South Bend-Elkhart, IN","NBC Sports"],
    ["Oklahoma Sooners","Norman, OK","Gaylord Family Oklahoma Memorial Stadium","OK","Oklahoma City, OK","SEC Network"],
    ["Texas A&M Aggies","College Station, TX","Kyle Field","TX","Waco-Temple-Bryan, TX","SEC Network"],
    ["Nebraska Cornhuskers","Lincoln, NE","Memorial Stadium","NE","Lincoln & Hastings-Kearney, NE","Big Ten Network"],
    ["Florida Gators","Gainesville, FL","Ben Hill Griffin Stadium","FL","Gainesville, FL","SEC Network"],
    ["Auburn Tigers","Auburn, AL","Jordan-Hare Stadium","AL","Columbus, GA (Opelika-Auburn)","SEC Network"],
    ["USC Trojans","Los Angeles, CA","L.A. Memorial Coliseum","CA","Los Angeles, CA","Big Ten Network"],
    ["Michigan State Spartans","East Lansing, MI","Spartan Stadium","MI","Lansing, MI","Big Ten Network"],
    ["Wisconsin Badgers","Madison, WI","Camp Randall Stadium","WI","Madison, WI","Big Ten Network"],
    ["Clemson Tigers","Clemson, SC","Memorial Stadium (Death Valley)","SC","Greenville-Spartanburg-Asheville-Anderson","ACC Network"],
    ["Oregon Ducks","Eugene, OR","Autzen Stadium","OR","Eugene, OR","Big Ten Network"],
    ["Florida State Seminoles","Tallahassee, FL","Doak Campbell Stadium","FL","Tallahassee-Thomasville, FL","ACC Network"],
    ["South Carolina Gamecocks","Columbia, SC","Williams-Brice Stadium","SC","Columbia, SC","SEC Network"],
    ["Iowa Hawkeyes","Iowa City, IA","Kinnick Stadium","IA","Cedar Rapids-Waterloo-Iowa City, IA","Big Ten Network"],
    ["Washington Huskies","Seattle, WA","Husky Stadium","WA","Seattle-Tacoma, WA","Big Ten Network"],
    ["Ole Miss Rebels","Oxford, MS","Vaught-Hemingway Stadium","MS","Memphis, TN","SEC Network"],
    ["Arkansas Razorbacks","Fayetteville, AR","Razorback Stadium","AR","Fort Smith-Fayetteville-Springdale-Rogers, AR","SEC Network"],
    ["Miami Hurricanes","Miami Gardens, FL","Hard Rock Stadium","FL","Miami-Fort Lauderdale, FL","ACC Network"],
    ["UCLA Bruins","Pasadena, CA","Rose Bowl","CA","Los Angeles, CA","Big Ten Network"],
    ["Kentucky Wildcats","Lexington, KY","Kroger Field","KY","Lexington, KY","SEC Network"],
    ["Missouri Tigers","Columbia, MO","Faurot Field","MO","Columbia-Jefferson City, MO","SEC Network"],
    ["Virginia Tech Hokies","Blacksburg, VA","Lane Stadium","VA","Roanoke-Lynchburg, VA","ACC Network"],
    ["Oklahoma State Cowboys","Stillwater, OK","Boone Pickens Stadium","OK","Tulsa, OK","ESPN+"],
    ["Texas Tech Red Raiders","Lubbock, TX","Jones AT&T Stadium","TX","Lubbock, TX","ESPN+"],
    ["Utah Utes","Salt Lake City, UT","Rice-Eccles Stadium","UT","Salt Lake City, UT","ESPN+"],
    ["Colorado Buffaloes","Boulder, CO","Folsom Field","CO","Denver, CO","ESPN+"],
    ["Mississippi State Bulldogs","Starkville, MS","Davis Wade Stadium","MS","Columbus-Tupelo-West Point, MS","SEC Network"],
    ["Minnesota Golden Gophers","Minneapolis, MN","Huntington Bank Stadium","MN","Minneapolis-St. Paul, MN","Big Ten Network"],
    ["North Carolina Tar Heels","Chapel Hill, NC","Kenan Stadium","NC","Raleigh-Durham, NC","ACC Network"],
    ["Louisville Cardinals","Louisville, KY","L&N Federal Credit Union Stadium","KY","Louisville, KY","ACC Network"],
    ["West Virginia Mountaineers","Morgantown, WV","Milan Puskar Stadium","WV","Clarksburg-Weston, WV","ESPN+"],
    ["Kansas State Wildcats","Manhattan, KS","Bill Snyder Family Stadium","KS","Topeka, KS","ESPN+"]
  ];

  const PRO = [
    ["Arizona Cardinals","Glendale, AZ","State Farm Stadium","AZ","Phoenix, AZ","NFL Network"],
    ["Atlanta Falcons","Atlanta, GA","Mercedes-Benz Stadium","GA","Atlanta, GA","NFL Network"],
    ["Baltimore Ravens","Baltimore, MD","M&T Bank Stadium","MD","Baltimore, MD","NFL Network"],
    ["Buffalo Bills","Orchard Park, NY","Highmark Stadium","NY","Buffalo, NY","NFL Network"],
    ["Carolina Panthers","Charlotte, NC","Bank of America Stadium","NC","Charlotte, NC","NFL Network"],
    ["Chicago Bears","Chicago, IL","Soldier Field","IL","Chicago, IL","NFL Network"],
    ["Cincinnati Bengals","Cincinnati, OH","Paycor Stadium","OH","Cincinnati, OH","NFL Network"],
    ["Cleveland Browns","Cleveland, OH","Huntington Bank Field","OH","Cleveland-Akron, OH","NFL Network"],
    ["Dallas Cowboys","Arlington, TX","AT&T Stadium","TX","Dallas-Fort Worth, TX","NFL Network"],
    ["Denver Broncos","Denver, CO","Empower Field at Mile High","CO","Denver, CO","NFL Network"],
    ["Detroit Lions","Detroit, MI","Ford Field","MI","Detroit, MI","NFL Network"],
    ["Green Bay Packers","Green Bay, WI","Lambeau Field","WI","Green Bay, WI","NFL Network"],
    ["Houston Texans","Houston, TX","NRG Stadium","TX","Houston, TX","NFL Network"],
    ["Indianapolis Colts","Indianapolis, IN","Lucas Oil Stadium","IN","Indianapolis, IN","NFL Network"],
    ["Jacksonville Jaguars","Jacksonville, FL","EverBank Stadium","FL","Jacksonville, FL","NFL Network"],
    ["Kansas City Chiefs","Kansas City, MO","Arrowhead Stadium","MO","Kansas City, MO","NFL Network"],
    ["Las Vegas Raiders","Las Vegas, NV","Allegiant Stadium","NV","Las Vegas, NV","NFL Network"],
    ["Los Angeles Chargers","Inglewood, CA","SoFi Stadium","CA","Los Angeles, CA","NFL Network"],
    ["Los Angeles Rams","Inglewood, CA","SoFi Stadium","CA","Los Angeles, CA","NFL Network"],
    ["Miami Dolphins","Miami Gardens, FL","Hard Rock Stadium","FL","Miami-Fort Lauderdale, FL","NFL Network"],
    ["Minnesota Vikings","Minneapolis, MN","U.S. Bank Stadium","MN","Minneapolis-St. Paul, MN","NFL Network"],
    ["New England Patriots","Foxborough, MA","Gillette Stadium","MA","Boston, MA","NFL Network"],
    ["New Orleans Saints","New Orleans, LA","Caesars Superdome","LA","New Orleans, LA","NFL Network"],
    ["New York Giants","East Rutherford, NJ","MetLife Stadium","NJ","New York, NY","NFL Network"],
    ["New York Jets","East Rutherford, NJ","MetLife Stadium","NJ","New York, NY","NFL Network"],
    ["Philadelphia Eagles","Philadelphia, PA","Lincoln Financial Field","PA","Philadelphia, PA","NFL Network"],
    ["Pittsburgh Steelers","Pittsburgh, PA","Acrisure Stadium","PA","Pittsburgh, PA","NFL Network"],
    ["San Francisco 49ers","Santa Clara, CA","Levi's Stadium","CA","San Francisco-Oakland-San Jose, CA","NFL Network"],
    ["Seattle Seahawks","Seattle, WA","Lumen Field","WA","Seattle-Tacoma, WA","NFL Network"],
    ["Tampa Bay Buccaneers","Tampa, FL","Raymond James Stadium","FL","Tampa-St. Petersburg, FL","NFL Network"],
    ["Tennessee Titans","Nashville, TN","Nissan Stadium","TN","Nashville, TN","NFL Network"],
    ["Washington Commanders","Landover, MD","Northwest Stadium","MD","Washington, DC","NFL Network"]
  ];

  // ---- Normalize rosters into objects ---------------------------------------
  function pack(arr, league) {
    return arr.map(([name, city, venue, state, dma, rsn]) => ({
      name, city, venue, state, dma, league, rsn,
      nick: name.split(" ").slice(-1)[0],
      stateName: (STATES[state] || [state])[0],
      dmaHouseholds: DMA_HOUSEHOLDS[dma] || null,
      stateHouseholds: STATES[state] ? Math.round(STATES[state][1] / AVG_HH_SIZE) : null
    }));
  }
  const TEAMS = { college: pack(COLLEGE, "college"), pro: pack(PRO, "pro") };

  /* ===========================================================================
     LOCAL / TEAM PODCASTS
     ---------------------------------------------------------------------------
     The old build only ever surfaced national shows (Finebaum, McAfee, Simmons),
     which a local advertiser can't relate to. Every team now gets local + team
     shows first, national second.

     LOCAL_OVERRIDES holds flagship shows we're confident about. Everything else
     falls back to two patterns that genuinely exist league-wide:
       - "Locked On <Nickname>"  (Locked On Podcast Network — one per team)
       - "<Team> Official Team Podcast"
     Treat this list as SUGGESTED inventory — confirm availability at buy time.
     Todd: add/replace shows here, nowhere else.
  =========================================================================== */
  const LOCAL_OVERRIDES = {
    "Cincinnati Bengals": [["The Bengals Booth Podcast","Bengals.com — official"]],
    "Cleveland Browns":   [["Cleveland Browns Daily","Browns.com — official"],
                           ["Orange and Brown Talk","cleveland.com"]],
    "Pittsburgh Steelers":[["The Standard is the Standard","Steelers.com — official"]],
    "Green Bay Packers":  [["Packers Unscripted","Packers.com — official"]],
    "Buffalo Bills":      [["One Bills Live","Bills.com — official"]],
    "Philadelphia Eagles":[["Eagles Insider Podcast","Eagles.com — official"]],
    "New England Patriots":[["Patriots Unfiltered","Patriots.com — official"]],
    "Detroit Lions":      [["Twentyman in the Huddle","Lions.com — official"]],
    "Chicago Bears":      [["Hoge & Jahns","Chicago sports talk"]],
    "Denver Broncos":     [["Broncos Country Tonight","Denver sports radio"]],
    "Dallas Cowboys":     [["Blogging The Boys","SB Nation — Dallas"]],
    "Ohio State Buckeyes":[["Buckeye Talk","cleveland.com"]],
    "Georgia Bulldogs":   [["DawgNation Daily","DawgNation — Atlanta"]],
    "Penn State Nittany Lions":[["The Blue White Show","Blue White Illustrated"]]
  };

  function localPodcasts(team) {
    if (!team) return [];
    const out = (LOCAL_OVERRIDES[team.name] || []).map(([name, network]) =>
      ({ name, network, local: true }));
    out.push({ name: `Locked On ${team.nick}`, network: "Locked On Podcast Network", local: true });
    if (team.league === "pro")
      out.push({ name: `${team.name} Official Team Podcast`, network: team.name, local: true });
    else
      out.push({ name: `${team.name} Official Athletics Podcast`, network: `${team.name} Athletics` , local: true });
    // de-dupe by name, cap at 4
    const seen = new Set();
    return out.filter(p => !seen.has(p.name) && seen.add(p.name)).slice(0, 4);
  }

  /* ===========================================================================
     BRAND LOGOS
     Name -> domain. index.html renders <img src="https://logo.clearbit.com/DOMAIN">
     with a favicon fallback and finally a coloured monogram tile, so a missing
     logo never leaves a blank box.
  =========================================================================== */
  const BRAND_DOMAINS = {
    // streaming video / CTV
    "hulu":"hulu.com", "hulu + live tv":"hulu.com", "peacock":"peacocktv.com",
    "paramount+":"paramountplus.com", "paramount plus":"paramountplus.com",
    "espn":"espn.com", "espn+":"espn.com", "espn plus":"espn.com",
    "max":"max.com", "hbo max":"max.com", "netflix":"netflix.com",
    "prime video":"primevideo.com", "amazon prime video":"primevideo.com",
    "tubi":"tubitv.com", "roku":"roku.com", "the roku channel":"roku.com",
    "roku channel":"roku.com", "pluto tv":"pluto.tv", "fubo":"fubo.tv",
    "fubotv":"fubo.tv", "youtube tv":"youtube.com", "youtube":"youtube.com",
    "sling tv":"sling.com", "sling":"sling.com", "philo":"philo.com",
    "directv stream":"directv.com", "vizio":"vizio.com", "samsung tv plus":"samsung.com",
    "lg channels":"lg.com", "crackle":"crackle.com", "plex":"plex.tv",
    // streaming audio
    "spotify":"spotify.com", "pandora":"pandora.com", "iheartradio":"iheart.com",
    "iheart":"iheart.com", "audacy":"audacy.com", "siriusxm":"siriusxm.com",
    "sirius xm":"siriusxm.com", "amazon music":"music.amazon.com",
    "apple podcasts":"apple.com", "apple music":"apple.com",
    "soundcloud":"soundcloud.com", "tunein":"tunein.com", "deezer":"deezer.com",
    "triton digital":"tritondigital.com", "megaphone":"megaphone.fm",
    // sports networks
    "fox sports":"foxsports.com", "fs1":"foxsports.com", "fs2":"foxsports.com",
    "cbs sports":"cbssports.com", "cbs sports network":"cbssports.com",
    "nbc sports":"nbcsports.com", "tnt":"tntdrama.com", "tnt sports":"tntsports.com",
    "nfl network":"nfl.com", "nfl":"nfl.com", "big ten network":"bigten.org",
    "btn":"bigten.org", "sec network":"espn.com", "acc network":"espn.com",
    "fanduel sports network":"fanduelsportsnetwork.com",
    "bally sports":"fanduelsportsnetwork.com",
    "the athletic":"theathletic.com", "bleacher report":"bleacherreport.com",
    "cbs":"cbs.com", "nbc":"nbc.com", "abc":"abc.com", "fox":"fox.com",
    "marquee sports network":"marqueesportsnetwork.com",
    "msg network":"msgnetworks.com", "root sports":"rootsports.com",
    "espn radio":"espn.com", "barstool sports":"barstoolsports.com",
    "the ringer":"theringer.com", "locked on podcast network":"lockedonpodcasts.com",
    "locked on":"lockedonpodcasts.com", "sb nation":"sbnation.com",
    "wondery":"wondery.com", "cumulus":"cumulusmedia.com"
  };

  function brandDomain(name) {
    if (!name) return null;
    const k = String(name).toLowerCase().trim().replace(/\s+/g, " ");
    if (BRAND_DOMAINS[k]) return BRAND_DOMAINS[k];
    // longest-key substring match, so "Hulu + Live TV (with sports)" still resolves
    let best = null;
    for (const key in BRAND_DOMAINS) {
      if (key.length < 3) continue;
      if (k.includes(key) && (!best || key.length > best.length)) best = key;
    }
    return best ? BRAND_DOMAINS[best] : null;
  }

  /* ===========================================================================
     RELATED-AUDIENCE ICONS  (keyword -> icon key rendered as inline SVG)
  =========================================================================== */
  const AUDIENCE_ICONS = [
    [/bet|dfs|fantasy|wager|sportsbook/i,        "bet"],
    [/truck|auto|car|vehicle|dealer/i,           "auto"],
    [/pizza|qsr|restaurant|dining|food|delivery/i,"food"],
    [/beer|spirit|liquor|alcohol|tailgat|bbq|grill/i,"beer"],
    [/home improve|hardware|contractor|remodel|hvac|roof/i,"home"],
    [/apparel|sportswear|fan gear|merch|retail|shop/i,"shirt"],
    [/travel|hotel|airline|vacation|ticket/i,    "travel"],
    [/bank|financ|insur|invest|mortgage|credit/i,"finance"],
    [/health|fitness|gym|medical|dental|wellness/i,"health"],
    [/tech|mobile|electronic|gaming|console/i,   "tech"]
  ];
  function audienceIcon(name) {
    for (const [re, key] of AUDIENCE_ICONS) if (re.test(String(name || ""))) return key;
    return "target";
  }

  /* ===========================================================================
     PACKAGES — single source of truth shared by the page and the results panel
  =========================================================================== */
  const PACKAGES = [
    { id:"tailgater",   name:"The Tailgater",   price:2500, track:"audio",
      tag:"", blurb:"The complete audio flow, Thursday through Monday.",
      features:["The complete 3-block audio flow (Thurs–Mon)",
                "Creative production — 3 custom audio spots",
                "300×250 clickable companion banners",
                "38 streaming platforms & 33 podcast networks"] },
    { id:"champion",    name:"The Champion",    price:4500, track:"audio",
      tag:"MOST LOCAL REACH", blurb:"Everything in Tailgater, plus stadium-level targeting.",
      features:["Everything in The Tailgater, plus:",
                "The Stadium Display Add-On (Location Lookback)",
                "Capture device IDs at the stadium on game day",
                "Retarget those exact fans Sunday & Monday"] },
    { id:"alumni",      name:"The Alumni",      price:3500, track:"ctv",
      tag:"", blurb:"Baseline CTV penetration and standard digital display.",
      features:["High-impact CTV video impressions",
                "Standard programmatic display retargeting",
                "Standard campaign reporting"] },
    { id:"championship",name:"The Championship",price:6000, track:"ctv",
      tag:"RECOMMENDED", blurb:"Ultimate multi-screen saturation and stadium capture.",
      features:["Premium CTV impressions (highest-tier inventory)",
                "$2,000 dedicated to Venue Replay targeting",
                "Direct targeting of attendees & tailgaters",
                "Maximum frequency and localized dominance"] }
  ];

  // ---- Lookups & model -------------------------------------------------------
  function findTeam(league, name) {
    if (!name) return null;
    const q = String(name).trim().toLowerCase();
    const list = TEAMS[league] || [];
    return list.find(t => t.name.toLowerCase() === q)
        || list.find(t => t.name.toLowerCase().includes(q) && q.length > 2)
        || null;
  }

  function marketForScope(team, scope) {
    if (scope === "national")
      return { label: "United States", households: NATIONAL.households,
               population: NATIONAL.population, geo: "nationwide" };
    if (scope === "regional")
      return { label: team.stateName, households: team.stateHouseholds,
               population: STATES[team.state] ? STATES[team.state][1] : null,
               geo: `the state of ${team.stateName}` };
    return { label: `${team.dma} DMA`, households: team.dmaHouseholds,
             population: team.dmaHouseholds ? Math.round(team.dmaHouseholds * AVG_HH_SIZE) : null,
             geo: `the ${team.dma} media market (DMA)` };
  }

  /* ---------------------------------------------------------------------------
     computeMetrics — a strict narrowing funnel.
     Every value below is smaller than the one above it, in HOMES. Only the last
     step changes unit, to SCREENS, and reports screensPerHome so the jump is
     self-explaining.
  --------------------------------------------------------------------------- */
  function computeMetrics(team, scope) {
    const m = marketForScope(team, scope);
    const homes = m.households || 0;

    const fanPct = FAN_HH_PENETRATION[scope] || FAN_HH_PENETRATION.local;
    const fanHomes = Math.round(homes * fanPct);
    const reachableHomes = Math.round(fanHomes * ADDRESSABLE_RATE);

    // Screens are counted INSIDE the reachable fan homes only — this is the fix.
    const deviceMix = {
      phones:      Math.round(reachableHomes * DEVICE_PER_HH.phones      * MATCH_RATE),
      computers:   Math.round(reachableHomes * DEVICE_PER_HH.computers   * MATCH_RATE),
      tablets:     Math.round(reachableHomes * DEVICE_PER_HH.tablets     * MATCH_RATE),
      connectedTV: Math.round(reachableHomes * DEVICE_PER_HH.connectedTV * MATCH_RATE)
    };
    const screens = deviceMix.phones + deviceMix.computers + deviceMix.tablets + deviceMix.connectedTV;
    const screensPerHome = reachableHomes ? screens / reachableHomes : 0;

    // People — used for pricing brackets and ONE standalone stat line.
    // Never render this next to a household count.
    const fansInHomes = Math.round(fanHomes * FANS_PER_FAN_HH);

    return {
      scope, scopeLabel: m.label, geo: m.geo,

      // the funnel, in order
      homes,                 // 1. every home in the market
      fanHomes,              // 2. homes that follow this team
      reachableHomes,        // 3. homes we can address programmatically
      screens,               // 4. screens inside those homes
      screensPerHome,

      fanPct,
      addressablePct: ADDRESSABLE_RATE,
      fansInHomes,
      deviceMix,

      // ---- legacy aliases: keep server.js / lib/pdf.js payloads working ----
      households: homes,
      fanBase: fansInHomes,
      devices: { ...deviceMix, total: screens },
      matchable: screens
    };
  }

  function recommendPackage(focus) {
    if (focus === "ctv")
      return { name: "The Championship (CTV)", price: "$6,000/mo", id: "championship",
        note: "Premium CTV inventory with $2,000 dedicated to Venue Replay stadium targeting." };
    if (focus === "both")
      return { name: "Full-Field Coverage (Audio + CTV)", price: "from $6,000/mo", id: "championship",
        note: "Multi-screen saturation — CTV awareness feeding streaming audio and mobile Venue Replay." };
    return { name: "The Champion (Audio)", price: "$4,500/mo", id: "champion",
      note: "Full 3-block audio flow plus the Stadium Display Add-On for local retargeting." };
  }

  // Static fallback used when the AI service is unavailable ---------------------
  function fallbackRecommendations(focus, team) {
    const audioSvc = ["Spotify","Pandora","iHeartRadio","Audacy","Amazon Music","SiriusXM"];
    const ctvSvc = ["Hulu + Live TV","YouTube TV","Sling TV","Fubo","ESPN+","Paramount+","Peacock","The Roku Channel"];
    const svc = focus === "ctv" ? ctvSvc : focus === "both" ? [...ctvSvc.slice(0,5), ...audioSvc.slice(0,4)] : audioSvc;
    const wantAudio = focus === "audio" || focus === "both";

    const national = [
      { name: "The Pat McAfee Show", network: "ESPN", local: false },
      { name: "The Bill Simmons Podcast", network: "The Ringer", local: false },
      { name: "The Solid Verbal", network: "College football", local: false },
      { name: "The Paul Finebaum Show", network: "ESPN / SEC Network", local: false }
    ];

    const nets = ["ESPN","Fox Sports","CBS Sports","NBC Sports","NFL Network"];
    if (team && team.rsn && !nets.includes(team.rsn)) nets.unshift(team.rsn);

    return {
      streamingServices: svc.map(name => ({ name, why: "Common football-fan inventory" })),
      podcasts: wantAudio ? [...localPodcasts(team), ...national].slice(0, 8) : [],
      sportsNetworks: nets.slice(0, 6).map(name => ({ name, why: "Carries this team's games" })),
      relatedAudiences: ["Sports betting & DFS","Trucks & domestic auto","QSR & pizza delivery",
        "Beer, spirits & tailgate","Sportswear & fan gear","Home improvement"]
        .map(name => ({ name, why: "Indexes high with football fans" }))
    };
  }

  const DATA = {
    TEAMS, STATES, DMA_HOUSEHOLDS, NATIONAL, PACKAGES,
    AVG_HH_SIZE, DEVICE_PER_HH, MATCH_RATE,
    FAN_HH_PENETRATION, FAN_PENETRATION: FAN_HH_PENETRATION, // legacy alias
    ADDRESSABLE_RATE, FANS_PER_FAN_HH,
    findTeam, marketForScope, computeMetrics, recommendPackage, fallbackRecommendations,
    localPodcasts, brandDomain, audienceIcon, BRAND_DOMAINS
  };

  if (typeof module !== "undefined" && module.exports) module.exports = DATA;
  else root.S1_DATA = DATA;

})(typeof self !== "undefined" ? self : this);
