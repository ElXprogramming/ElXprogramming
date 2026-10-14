#!/usr/bin/env python3
"""Generate neon-styled stat cards (SVG) for the profile README.

Usage:
    GH_TOKEN=... GITHUB_REPOSITORY_OWNER=ElXprogramming python scripts/update_stats.py
    python scripts/update_stats.py --demo      # render with sample data (no network)

Outputs (in ./assets): scorecard.svg, streak.svg, languages.svg, activity.svg, footer.svg
Only the Python standard library is used.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

# --------------------------------------------------------------------------- #
# Theme: one neon accent on dark, everything else neutral grey.
# --------------------------------------------------------------------------- #
ACCENT = "#ff2e88"
BG = "#0d1117"
TEXT = "#f0f6fc"
MUTED = "#8b949e"
DIM = "#3b2a35"       # border for unranked / empty boxes
EMPTY_CELL = "#1b1520"

WIDTH = 830           # inner width of every card
MARGIN = 12           # room around the boxes for the neon glow
GAP = 16

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Grade thresholds, highest first: S, A, B, C, D.  Below the last one = unranked.
THRESHOLDS = {
    "commits":   [1000, 500, 100, 20, 1],
    "repos":     [50, 25, 10, 3, 1],
    "languages": [10, 8, 6, 4, 1],
    "followers": [500, 100, 25, 10, 1],
    "prs":       [100, 50, 20, 5, 1],
    "issues":    [100, 50, 20, 5, 1],
    "stars":     [500, 100, 25, 5, 1],
}
LETTERS = {5: "S", 4: "A", 3: "B", 2: "C", 1: "D", 0: "\u2013"}


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def gql(query: str, token: str) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-readme-stats",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL error: {payload['errors']}")
    return payload["data"]


def fetch(login: str, token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9-]+", login):
        raise ValueError(f"Suspicious login: {login!r}")

    created = gql(f'{{ user(login: "{login}") {{ createdAt }} }}', token)["user"]["createdAt"]
    first_year = int(created[:4])
    now = datetime.now(timezone.utc)

    years = []
    for y in range(first_year, now.year + 1):
        start = f"{y}-01-01T00:00:00Z"
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ") if y == now.year else f"{y}-12-31T23:59:59Z"
        years.append(
            f'y{y}: contributionsCollection(from: "{start}", to: "{end}") {{'
            " totalCommitContributions"
            " contributionCalendar { weeks { contributionDays { date contributionCount } } } }"
        )

    query = f"""{{
      user(login: "{login}") {{
        followers {{ totalCount }}
        pullRequests {{ totalCount }}
        issues {{ totalCount }}
        repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100) {{
          totalCount
          nodes {{
            stargazerCount
            languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
              edges {{ size node {{ name }} }}
            }}
          }}
        }}
        {" ".join(years)}
      }}
    }}"""
    user = gql(query, token)["user"]

    commits = 0
    days: dict[date, int] = {}
    for y in range(first_year, now.year + 1):
        block = user[f"y{y}"]
        commits += block["totalCommitContributions"]
        for week in block["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[date.fromisoformat(d["date"])] = d["contributionCount"]

    stars = 0
    langs: dict[str, int] = {}
    for repo in user["repositories"]["nodes"]:
        stars += repo["stargazerCount"]
        for edge in repo["languages"]["edges"]:
            langs[edge["node"]["name"]] = langs.get(edge["node"]["name"], 0) + edge["size"]

    return {
        "commits": commits,
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "stars": stars,
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "languages": sorted(langs.items(), key=lambda kv: kv[1], reverse=True),
        "days": days,
    }


def demo_data() -> dict:
    rnd = random.Random(7)
    today = datetime.now(timezone.utc).date()
    days = {}
    for i in range(400):
        d = today - timedelta(days=i)
        days[d] = 0 if rnd.random() < 0.55 else rnd.randint(1, 9)
    days[today], days[today - timedelta(days=1)] = 3, 2
    return {
        "commits": 23, "prs": 0, "issues": 0, "stars": 0, "repos": 3, "followers": 6,
        "languages": [("TypeScript", 7194), ("Shell", 2198), ("HTML", 149), ("CSS", 428), ("JavaScript", 31)],
        "days": days,
    }


# --------------------------------------------------------------------------- #
# Maths
# --------------------------------------------------------------------------- #
def grade(key: str, value: int) -> int:
    for i, threshold in enumerate(THRESHOLDS[key]):
        if value >= threshold:
            return 5 - i
    return 0


def streaks(days: dict[date, int], today: date) -> tuple[int, int]:
    longest = run = 0
    prev = None
    for d in sorted(days):
        if days[d] > 0:
            run = run + 1 if prev is not None and (d - prev).days == 1 else 1
            longest = max(longest, run)
        else:
            run = 0
        prev = d

    current = 0
    d = today
    if days.get(d, 0) == 0:          # today may simply not have happened yet
        d -= timedelta(days=1)
    while days.get(d, 0) > 0:
        current += 1
        d -= timedelta(days=1)
    return current, longest


# --------------------------------------------------------------------------- #
# SVG building blocks
# --------------------------------------------------------------------------- #
def svg_open(w: float, h: float) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img">'
        "<style>text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono',monospace}</style>"
        '<defs><filter id="glow" x="-20%" y="-30%" width="140%" height="160%">'
        '<feGaussianBlur stdDeviation="4"/></filter></defs>'
    )


def box(x, y, w, h, label, value, *, grade_letter=None, unit="", lit=True, hero=False) -> str:
    stroke = ACCENT if lit else DIM
    out = []
    if lit:  # blurred copy underneath = neon glow
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" '
            f'stroke="{ACCENT}" stroke-width="3" opacity=".7" filter="url(#glow)"/>'
        )
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{BG}"/>')
    if hero:
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{ACCENT}" fill-opacity=".12"/>')
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" '
        f'stroke="{stroke}" stroke-width="1.5"/>'
    )
    out.append(
        f'<text x="{x + 18}" y="{y + 30}" font-size="11" letter-spacing="2" fill="{MUTED}">{escape(label.upper())}</text>'
    )
    size = 46 if hero else 34
    fill = ACCENT if hero else (TEXT if lit else MUTED)
    unit_tag = f'<tspan font-size="13" font-weight="400" fill="{MUTED}" dx="6">{escape(unit)}</tspan>' if unit else ""
    out.append(
        f'<text x="{x + 18}" y="{y + h - 24}" font-size="{size}" font-weight="700" fill="{fill}">'
        f"{escape(str(value))}{unit_tag}</text>"
    )
    if grade_letter is not None:
        cx, cy = x + w - 46, y + 14
        chip = ACCENT if lit else MUTED
        out.append(
            f'<rect x="{cx}" y="{cy}" width="32" height="26" rx="7" fill="none" stroke="{chip}" stroke-width="1.5"/>'
            f'<text x="{cx + 16}" y="{cy + 18}" font-size="14" font-weight="700" fill="{chip}" '
            f'text-anchor="middle">{escape(grade_letter)}</text>'
        )
    return "".join(out)


def card_frame(x, y, w, h) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" stroke="{ACCENT}" '
        f'stroke-width="3" opacity=".7" filter="url(#glow)"/>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{BG}" stroke="{ACCENT}" stroke-width="1.5"/>'
    )


def fmt(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def scorecard(data: dict) -> str:
    langs = len(data["languages"])
    items = [
        ("commits", "Commits", data["commits"]),
        ("prs", "Pull Requests", data["prs"]),
        ("issues", "Issues", data["issues"]),
        ("stars", "Stars Earned", data["stars"]),
        ("repos", "Repositories", data["repos"]),
        ("followers", "Followers", data["followers"]),
        ("languages", "Polyglot", langs),
    ]
    grades = {key: grade(key, value) for key, _, value in items}
    ranked = [g for g in grades.values() if g > 0]
    overall = int(sum(ranked) / len(ranked) + 0.5) if ranked else 0

    cols, bh = 4, 104
    bw = (WIDTH - (cols - 1) * GAP) / cols
    total_w = WIDTH + 2 * MARGIN
    total_h = 2 * bh + GAP + 2 * MARGIN
    parts = [svg_open(total_w, total_h)]

    for i, (key, label, value) in enumerate(items):
        row, col = divmod(i, cols)
        g = grades[key]
        parts.append(box(
            MARGIN + col * (bw + GAP), MARGIN + row * (bh + GAP), bw, bh, label, fmt(value),
            grade_letter=LETTERS[g], lit=g > 0, unit="langs" if key == "languages" else "",
        ))
    parts.append(box(
        MARGIN + 3 * (bw + GAP), MARGIN + (bh + GAP), bw, bh, "Overall", LETTERS[overall],
        lit=overall > 0, hero=True,
    ))
    parts.append("</svg>")
    return "".join(parts)


def streak_card(data: dict) -> str:
    today = datetime.now(timezone.utc).date()
    current, longest = streaks(data["days"], today)
    window_start = today - timedelta(days=364)
    active = sum(1 for d, c in data["days"].items() if d >= window_start and c > 0)

    bh = 104
    bw = (WIDTH - 2 * GAP) / 3
    parts = [svg_open(WIDTH + 2 * MARGIN, bh + 2 * MARGIN)]
    for i, (label, value, unit) in enumerate([
        ("Current Streak", current, "days"),
        ("Longest Streak", longest, "days"),
        ("Active Days", active, "this year"),
    ]):
        parts.append(box(MARGIN + i * (bw + GAP), MARGIN, bw, bh, label, fmt(value), unit=unit, lit=value > 0))
    parts.append("</svg>")
    return "".join(parts)


def languages_card(data: dict) -> str:
    h = 128
    total_w = WIDTH + 2 * MARGIN
    parts = [svg_open(total_w, h + 2 * MARGIN), card_frame(MARGIN, MARGIN, WIDTH, h)]
    parts.append(
        f'<text x="{MARGIN + 18}" y="{MARGIN + 30}" font-size="11" letter-spacing="2" fill="{MUTED}">TOP LANGUAGES</text>'
    )

    ranked_langs = sorted(data["languages"], key=lambda kv: kv[1], reverse=True)
    langs = ranked_langs[:5]
    total = sum(size for _, size in ranked_langs) or 1
    if not langs:
        parts.append(f'<text x="{MARGIN + 18}" y="{MARGIN + 80}" font-size="14" fill="{MUTED}">No data yet</text>')
        parts.append("</svg>")
        return "".join(parts)

    bar_x, bar_y, bar_w, bar_h = MARGIN + 18, MARGIN + 46, WIDTH - 36, 10
    parts.append(
        f'<clipPath id="bar"><rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5"/></clipPath>'
        f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5" fill="{EMPTY_CELL}"/>'
        '<g clip-path="url(#bar)">'
    )
    opacities = [1, 0.78, 0.58, 0.42, 0.28]
    x = bar_x
    for (name, size), op in zip(langs, opacities):
        seg = bar_w * size / total
        parts.append(f'<rect x="{x:.2f}" y="{bar_y}" width="{seg:.2f}" height="{bar_h}" fill="{ACCENT}" fill-opacity="{op}"/>')
        x += seg
    parts.append("</g>")

    col_w = bar_w / 5
    for i, ((name, size), op) in enumerate(zip(langs, opacities)):
        lx = bar_x + i * col_w
        ly = MARGIN + 92
        parts.append(
            f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{ACCENT}" fill-opacity="{op}"/>'
            f'<text x="{lx + 18}" y="{ly}" font-size="13" fill="{TEXT}">{escape(name)}</text>'
            f'<text x="{lx + 18}" y="{ly + 18}" font-size="12" fill="{MUTED}">{size / total * 100:.1f}%</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def activity_card(data: dict) -> str:
    today = datetime.now(timezone.utc).date()
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    start = last_sunday - timedelta(weeks=52)

    cell, pitch = 12, 15
    h = 200
    gx, gy = MARGIN + 18, MARGIN + 62
    total_w = WIDTH + 2 * MARGIN

    window = {d: c for d, c in data["days"].items() if start <= d <= today}
    peak = max(window.values(), default=0) or 1
    year_total = sum(window.values())

    parts = [svg_open(total_w, h + 2 * MARGIN), card_frame(MARGIN, MARGIN, WIDTH, h)]
    parts.append(
        f'<text x="{MARGIN + 18}" y="{MARGIN + 30}" font-size="11" letter-spacing="2" fill="{MUTED}">CONTRIBUTIONS \u00b7 LAST YEAR</text>'
        f'<text x="{MARGIN + WIDTH - 18}" y="{MARGIN + 30}" font-size="13" font-weight="700" fill="{ACCENT}" '
        f'text-anchor="end">{fmt(year_total)} total</text>'
    )

    # month labels
    labels, prev_month = [], None
    for w in range(53):
        month = (start + timedelta(weeks=w)).month
        if month != prev_month:
            labels.append((w, (start + timedelta(weeks=w)).strftime("%b")))
            prev_month = month
    if len(labels) > 1 and labels[1][0] - labels[0][0] < 3:
        labels.pop(0)
    for w, name in labels:
        parts.append(f'<text x="{gx + w * pitch}" y="{gy - 10}" font-size="10" fill="{MUTED}">{name}</text>')

    # cells
    for w in range(53):
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            if day > today:
                continue
            count = window.get(day, 0)
            if count == 0:
                fill, opacity = EMPTY_CELL, 1
            else:
                level = min(4, max(1, -(-4 * count // peak)))   # ceil(4*count/peak)
                fill, opacity = ACCENT, (0.25, 0.5, 0.75, 1)[level - 1]
            parts.append(
                f'<rect x="{gx + w * pitch}" y="{gy + d * pitch}" width="{cell}" height="{cell}" rx="3" '
                f'fill="{fill}" fill-opacity="{opacity}"/>'
            )

    # legend
    lx, ly = MARGIN + WIDTH - 18 - (5 * pitch) - 70, MARGIN + h - 20
    parts.append(f'<text x="{lx}" y="{ly + 10}" font-size="10" fill="{MUTED}" text-anchor="end">Less</text>')
    for i, op in enumerate((None, 0.25, 0.5, 0.75, 1)):
        fill, o = (EMPTY_CELL, 1) if op is None else (ACCENT, op)
        parts.append(
            f'<rect x="{lx + 8 + i * pitch}" y="{ly}" width="{cell}" height="{cell}" rx="3" fill="{fill}" fill-opacity="{o}"/>'
        )
    parts.append(f'<text x="{lx + 8 + 5 * pitch + 4}" y="{ly + 10}" font-size="10" fill="{MUTED}">More</text>')
    parts.append("</svg>")
    return "".join(parts)


def footer_card() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    w, h = WIDTH + 2 * MARGIN, 70
    return (
        svg_open(w, h)
        + '<defs><linearGradient id="line" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{ACCENT}"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></linearGradient></defs>'
        f'<rect x="{MARGIN}" y="22" width="{WIDTH}" height="3" rx="1.5" fill="url(#line)" filter="url(#glow)"/>'
        f'<rect x="{MARGIN}" y="22" width="{WIDTH}" height="2" rx="1" fill="url(#line)"/>'
        f'<text x="{w / 2}" y="52" font-size="11" letter-spacing="2" fill="{MUTED}" text-anchor="middle">'
        f"AUTO-UPDATED HOURLY \u00b7 {stamp} UTC</text></svg>"
    )


# --------------------------------------------------------------------------- #
def main() -> None:
    if "--demo" in sys.argv:
        data = demo_data()
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        login = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("PROFILE_LOGIN")
        if not token or not login:
            sys.exit("Set GH_TOKEN and GITHUB_REPOSITORY_OWNER (or PROFILE_LOGIN), or pass --demo.")
        data = fetch(login, token)

    ASSETS.mkdir(exist_ok=True)
    cards = {
        "scorecard.svg": scorecard(data),
        "streak.svg": streak_card(data),
        "languages.svg": languages_card(data),
        "activity.svg": activity_card(data),
        "footer.svg": footer_card(),
    }
    for name, content in cards.items():
        (ASSETS / name).write_text(content, encoding="utf-8")
        print(f"wrote assets/{name}")


if __name__ == "__main__":
    main()
