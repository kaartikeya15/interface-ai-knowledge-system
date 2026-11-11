"""What this system knows about one website in particular: Dupaco's.

The rest of `kb/` is a general engine: it parses rate tables, resolves facts by effective date and page authority,
keeps one view per snapshot, and searches passages. Everything below was learned by reading these snapshots.
Pointing the system at another credit union means writing a new version of this file, not changing the engine.
"""
import re

NAME = "Dupaco"
DOMAIN = "dupaco.com"           # links elsewhere (apply portals, partners) are not answers
NAME_WORDS = {"dupaco", "dupaco's"}          # the credit union naming itself carries no meaning in a question
ONLINE_BANKING = "Shine Online or Mobile Banking"

# --- Page kinds, from the URL path (see parse.page_kind) ---
RATE_SHEET_PATHS = ("/rates", "/business-rates")
NEWS_PREFIXES = ("/newsroom",)
BLOG_PREFIXES = ("/learn", "/business/resources", "/resources")   # plus dated paths such as /2018/09/25/...
FILE_PREFIXES = ("/hubfs",)
LANDING_SUFFIX = "-lp"
LANDING_PATHS = ("/offer-expired", "/switch-and-earn")   # promo pages that outlive their offers

# --- Pages with a known job ---
CONTACT_PATH = "/contact"      # branch locator, addresses and the main phone number
JOIN_PATH = "/about/join"      # membership eligibility
BRANCH_MARKER = "Dupaco Branch"   # on the contact page, precedes each branch address
PHONE = re.compile(r"\*\*Phone:\*\*\s*\[?(\d{3}-\d{3}-\d{4})")

# --- Boilerplate claims in press releases that drift over time (newest dated release wins) ---
BRANCH_COUNT = re.compile(r"\b(\d{2}) branch office locations\b")
SERVES = re.compile(rf"{NAME} serves residents in (\d+) counties throughout ([^.\n]+)")
CLAIMS = [
    ("branch count", BRANCH_COUNT, "{} branch office locations"),
    ("counties served", re.compile(r"serves residents in (\d+) counties"), "{} counties"),
]
RECOGNITION = ("forbes", "Forbes recognition count")   # releases count the award: "the fifth time"
FOUNDED = re.compile(rf"\b(?:[Ss]ince (1[89]\d\d|20[0-2]\d), (?:we|{NAME})|"
                     r"(?:founded|chartered|established) in (1[89]\d\d|20[0-2]\d))")

# --- The auto-loan calculator keeps its own copy of the rate table ---
CALC_ROW = re.compile(r"^\|\s*(20\d\d)[^|]*Model Year[^|]*\|\s*(\d+) Months\s*\|\s*(\d+\.\d+%)", re.M)

# --- Deposit products. Tables: which account a balance-tier table sits under (first match, nearest line) ---
TABLE_ACCOUNT_NAMES = [
    (r"health savings", "Health Savings Account"), (r"investor", "Investor's Choice Money Market"),
    (r"high-yield", "High-Yield Savings"), (r"1st rate", "1st Rate Checking"), (r"free checking", "Free Checking"),
    (r"holiday club", "Holiday Club Savings"), (r"launchpad", "Launchpad Savings"), (r"bright", "Bright Savings"),
    (r"you.name.it", "You Name It Savings"), (r"share savings", "Share Savings"),
]
# Questions: how members name those accounts (first match wins, so plain "savings" comes last)
QUESTION_ACCOUNT_NAMES = [
    ("Bright Savings", r"\bbright\b"), ("High-Yield Savings", r"high[- ]yield"), ("You Name It Savings", r"you name it"),
    ("Holiday Club Savings", r"holiday|christmas"), ("Launchpad Savings", r"launch ?pad"),
    ("Investor's Choice Money Market", r"money market|investor"), ("Health Savings Account", r"\bhsa\b|health savings"),
    ("1st Rate Checking", r"(1st|first) rate"), ("Free Checking", r"free checking"),
    ("Share Savings", r"share savings|savings"),
]

# --- Text quirks ---
TEXT_FIXES = [("hy-vee", "hyvee")]   # a store name that tokenises badly
