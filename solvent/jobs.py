"""
jobs.py — sample inbound work for the demo.

These simulate commission requests arriving in SOLVENT's queue (in production
they'd come from a web form, an email parser, or an API). Note job J3 is
deliberately unprofitable so you can watch the margin gate decline it.
"""

SAMPLE_JOBS = [
    {
        "id": "J1",
        "topic": "Competitive landscape for AI inference chips, 2026",
        "context": "VC doing diligence on a fabless startup.",
        "customer_email": "analyst@fund.example",
        "budget_cents": 4_900,        # customer will pay $49
        "est_tokens": 9_000,
        "market_data_calls": 2,
        "web_search_calls": 8,
    },
    {
        "id": "J2",
        "topic": "Stablecoin payment volumes and the take-rate outlook",
        "context": "Fintech PM sizing a new product line.",
        "customer_email": "pm@fintech.example",
        "budget_cents": 7_500,        # $75
        "est_tokens": 12_000,
        "market_data_calls": 3,
        "web_search_calls": 10,
    },
    {
        "id": "J3",
        "topic": "One-line definition of EBITDA",
        "context": "Student wants a cheap answer.",
        "customer_email": "student@school.example",
        "budget_cents": 600,          # $6 — below cost; should be DECLINED
        "est_tokens": 7_000,
        "market_data_calls": 2,
        "web_search_calls": 6,
    },
    {
        "id": "J4",
        "topic": "Edge-AI adoption in industrial robotics: 18-month outlook",
        "context": "Corp-dev team scoping an acquisition.",
        "customer_email": "corpdev@robotics.example",
        "budget_cents": 9_900,        # $99
        "est_tokens": 11_000,
        "market_data_calls": 3,
        "web_search_calls": 9,
    },
]
