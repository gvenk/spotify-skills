#!/usr/bin/env python3
"""Helper for the on-this-day skill: songs for a date, from artists born or died on it, album anniversaries and number ones.

Standard library only (Python 3.9+). It reads Wikipedia (read-only, en.wikipedia.org only), finds the songs on Spotify through
the Spotify desktop app's `spotify_cli`, and, only with the user's yes, adds them to the queue or saves them as a private playlist.

Commands:
  charts                                              the charts that are built in
  discover [--date YYYY-MM-DD] [--album-years round|all] [--chart ID ...] [--show N] [--max-number-ones N] --out FILE
                                                      everything for that date (default today), numbered, with the Spotify song found
  compose  --candidates FILE --out FILE (--auto [--count 20] [--per-angle 5] [--angles A,B] [--seed N] | --item N[:K|:album] ... [--shuffle])
                                                      choose what goes into the list
  queue    --selection FILE --confirm CODE [--position end|front] [--max-tracks 75] [--dry-run]
  playlist --selection FILE --confirm CODE [--name NAME] [--public] [--max-tracks 200] [--dry-run]
           (--public needs confirm_code_public instead of confirm_code; the default is a private playlist)
"""

import argparse
import datetime
import hashlib
import html
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")
PLAYLIST_URI = re.compile(r"spotify:playlist:[A-Za-z0-9]+")
PAGE_TITLE = re.compile(r"[\w ,.'&()!:/–\-]{1,200}")
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "on-this-day/1.0 (https://github.com/gvenk/spotify-skills; reads public Wikipedia pages)"
MAX_PAGE_BYTES = 6 * 1024 * 1024
MAX_GRID_CELLS = 2000000  # the largest real list has a few thousand cells; merged cells must not multiply them endlessly
MAX_PAGES = 600
MAX_YEARS = 130
TIMEOUT = 30
CHARTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts.json")


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


BIDI_CONTROLS = set("‪‫‬‭‮⁦⁧⁨⁩")


def clean_text(value, limit=300):
    """Untrusted text (a Wikipedia cell, a track title) made safe to print and to write back to Spotify:
    control characters and text-direction overrides are removed, whitespace becomes single spaces, length is capped."""
    kept = []
    for ch in str(value):
        if ch.isspace():
            kept.append(" ")
        elif unicodedata.category(ch) != "Cc" and ch not in BIDI_CONTROLS:
            kept.append(ch)
    return " ".join("".join(kept).split())[:limit]


def today():
    if os.environ.get("ONTHISDAY_TEST") == "1" and os.environ.get("ONTHISDAY_TODAY"):
        return datetime.date.fromisoformat(os.environ["ONTHISDAY_TODAY"])
    return datetime.date.today()


# ---------------------------------------------------------------- reading Wikipedia

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def api_url():
    """Wikipedia's API. A test can point it at a local server; nothing else can change the host."""
    override = os.environ.get("ONTHISDAY_API_URL")
    if override and os.environ.get("ONTHISDAY_TEST") == "1":
        p = urllib.parse.urlparse(override)
        if p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost"):
            return override
    return WIKI_API


_PAGES = {}
_PARSED = {}


def fetch_page(title):
    """The HTML of a Wikipedia page, or None if there is no such page. Read once per run."""
    if title in _PAGES:
        return _PAGES[title]
    if not PAGE_TITLE.fullmatch(title):
        die("%r is not an acceptable Wikipedia page title." % title)
    if len(_PAGES) >= MAX_PAGES:
        die("This would read more than %d Wikipedia pages." % MAX_PAGES, 4)
    query = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": "2",
                                    "disabletoc": "1", "redirects": "1"})
    req = urllib.request.Request(api_url() + "?" + query, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_PAGE_BYTES + 1)
    except urllib.error.HTTPError as e:
        die("Wikipedia answered %s for %r." % (e.code, title), 6)
    except (urllib.error.URLError, OSError) as e:
        die("Could not reach Wikipedia: %s" % e, 6)
    if len(raw) > MAX_PAGE_BYTES:
        die("The page %r is larger than %d MB; refusing to read it." % (title, MAX_PAGE_BYTES // 1048576), 6)
    try:
        data = json.loads(raw)
    except ValueError:
        die("Wikipedia gave an answer that is not JSON for %r." % title, 6)
    if isinstance(data, dict) and "error" in data:
        code = clean_text((data["error"] or {}).get("code", "unknown") if isinstance(data["error"], dict) else "unknown", 60)
        if code != "missingtitle":  # a busy or failing API is not "there is no such page"
            die("Wikipedia answered with an error (%s) for %r. Try again later; nothing is guessed." % (code, title), 6)
        _PAGES[title] = None
    elif not isinstance(data, dict):
        die("Wikipedia gave an unexpected answer for %r." % title, 6)
    else:
        text = (data.get("parse") or {}).get("text")
        _PAGES[title] = text if isinstance(text, str) else None
    return _PAGES[title]

class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.row, self.cell, self.skip, self.link = [], [], None, None, 0, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.stack.append({"cls": a.get("class", ""), "rows": []})
        elif not self.stack:
            return
        elif tag == "tr":
            self.row = []
            self.stack[-1]["rows"].append(self.row)
        elif tag in ("td", "th") and self.row is not None:
            def num(v):
                try:
                    return max(1, min(int(re.sub(r"\D", "", v) or 1), 200))
                except ValueError:
                    return 1
            self.cell = {"tag": tag, "text": "", "rs": num(a.get("rowspan", "1")), "cs": num(a.get("colspan", "1")), "links": []}
            self.row.append(self.cell)
        elif tag in ("sup", "style", "script"):
            self.skip += 1
        elif tag == "a" and self.cell is not None and not self.skip and str(a.get("href", "")).startswith("/wiki/") and a.get("title"):
            self.link = {"title": a["title"], "text": ""}  # the article an artist's name links to
        elif tag == "br" and self.cell is not None and not self.skip:
            self.cell["text"] += " / "

    def handle_endtag(self, tag):
        if tag == "table" and self.stack:
            self.tables.append(self.stack.pop())
        elif tag == "a" and self.link is not None:
            if self.cell is not None and self.link["text"].strip():
                self.cell["links"].append((self.link["text"].strip(), self.link["title"]))
            self.link = None
        elif tag in ("td", "th"):
            self.cell = None
        elif tag in ("sup", "style", "script") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell["text"] += data
            if self.link is not None:
                self.link["text"] += data


def expand(rows):
    """Table rows with merged cells (rowspan, colspan) filled in, so every row has every column.
    Raises ValueError when the filled-in table would be absurdly large (a hostile page)."""
    grid, pending, total = [], {}, 0
    for row in rows:
        out, col, it = [], 0, iter(row)
        while True:
            while col in pending:
                left, cell = pending[col]
                out.append(cell)
                total += 1
                if total > MAX_GRID_CELLS:
                    raise ValueError("a table that is far larger than any list of number ones")
                if left > 1:
                    pending[col] = (left - 1, cell)
                else:
                    del pending[col]
                col += 1
            cell = next(it, None)
            if cell is None:
                break
            for _ in range(cell["cs"]):
                out.append(cell)
                total += 1
                if total > MAX_GRID_CELLS:
                    raise ValueError("a table that is far larger than any list of number ones")
                if cell["rs"] > 1:
                    pending[col] = (cell["rs"] - 1, cell)
                col += 1
        grid.append(out)
    return grid


MONTHS = {}
for _i, _m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1):
    MONTHS[_m] = _i
    MONTHS[_m[:3]] = _i
MONTHS["sept"] = 9
_DATE_DMY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?(?:,?\s+(\d{4}))?")
_DATE_MDY = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?")


def parse_date(text, year_hint=None):
    """'January 5, 1980', '15 December 1979' and '5 January' (with the year of the page) -> date, or None."""
    text = " ".join(text.replace(" ", " ").split())
    for rx, order in ((_DATE_DMY, "dmy"), (_DATE_MDY, "mdy")):
        m = rx.search(text)
        if not m:
            continue
        day, mon, year = (m.group(1), m.group(2), m.group(3)) if order == "dmy" else (m.group(2), m.group(1), m.group(3))
        month = MONTHS.get(mon.lower())
        y = int(year) if year else year_hint
        if month and y:
            try:
                return datetime.date(y, month, int(day))
            except ValueError:
                return None
    return None


def clean_cell(text):
    text = html.unescape(text).replace(" ", " ")
    text = re.sub(r"\[[^\]]{1,12}\]", "", text)
    return clean_text(text.replace("†", "").replace("‡", "").replace("§", "").replace("*", ""), 300)


def cell_titles(text):
    quoted = re.findall(r"[“\"]([^”\"]{1,200})[”\"]", text)
    titles = [clean_cell(t) for t in quoted if clean_cell(t)]
    return titles or ([clean_cell(text)] if clean_cell(text) else [])


_DATE_HEADERS = ("reached number one", "week ending", "issue date", "chart date", "week of", "date reached", "date")
_BAD_DATE_HEADERS = ("released", "certified", "release")


def artist_alias(artist, links):
    """The artist as Wikipedia's article names them now: 'Jantje Smit' links to the article 'Jan Smit (singer)', so 'Jan Smit'.
    Returns None when no link gives a different name."""
    alias = artist
    for text, title in links:
        name = clean_cell(re.sub(r"\s*\([^)]*\)\s*$", "", title))
        shown = clean_cell(text)
        if name and shown and norm(name) != norm(shown) and shown in alias:
            alias = alias.replace(shown, name, 1)
    return alias if norm(alias) != norm(artist) else None


_NO_CHART = re.compile(r"^(?:no|not|n/?a|none)\b.*\b(?:chart|top\s*\d+|released|published|compiled|issued)\b|^no (?:chart|top)", re.I)


def is_no_chart(titles, artist):
    """A week without a chart ('No Top 40 released') is listed in some tables; it is not a song."""
    return any(_NO_CHART.search(t) for t in titles) or _NO_CHART.search(artist) is not None


def read_numberones(html_text, year_hint=None):
    """The rows of the number-one table of a Wikipedia list page: [{'date', 'titles', 'artist'}], oldest first.
    The columns are found by their headings. Raises ValueError when no table looks like a number-one list."""
    parser = _Tables()
    parser.feed(html_text)
    best, last_problem = [], "no table with a date, a song and an artist column"
    for table in parser.tables:
        if "wikitable" not in table["cls"]:
            continue
        try:
            grid = expand(table["rows"])
        except ValueError as e:
            last_problem = str(e)
            continue

        def is_header(r):
            return len(r) >= 3 and all(c["tag"] == "th" for c in r) and len({id(c) for c in r}) >= 3
        start = next((i for i, r in enumerate(grid) if is_header(r)), None)
        if start is None:
            continue
        end = start
        while end + 1 < len(grid) and is_header(grid[end + 1]):
            end += 1  # a heading over two rows: 'Hot Country Songs' above 'Title' and 'Artist(s)'
        header = grid[end]
        names = []
        for col in range(len(header)):
            parts = []
            for r in grid[start:end + 1]:
                t = " ".join(r[col]["text"].lower().split()) if col < len(r) else ""
                if t and t not in parts:
                    parts.append(t)
            names.append(" ".join(parts))

        def find(words, bad=()):
            for w in words:
                for i, n in enumerate(names):
                    if (n == w or n.startswith(w) or w in n) and not any(b in n for b in bad):
                        return i
            return None
        d_col = find(_DATE_HEADERS, _BAD_DATE_HEADERS)
        s_col = find(("song", "single", "title", "track"))
        a_col = find(("artist",))
        if d_col is None or s_col is None or a_col is None or len({d_col, s_col, a_col}) < 3:
            continue
        rows = []
        for r in grid[end + 1:]:
            if len({id(c) for c in r}) == 1 or len(r) <= max(d_col, s_col, a_col):
                continue  # a year heading that spans the table
            date = parse_date(r[d_col]["text"], year_hint)
            titles = cell_titles(r[s_col]["text"])
            artist = " ".join(clean_cell(r[a_col]["text"]).replace(" / ", " ").split())  # a line break between a name and "(featuring ...)"
            if date and titles and artist:
                rows.append({"date": date, "titles": titles, "artist": artist, "no_chart": is_no_chart(titles, artist),
                             "artist_now": artist_alias(artist, r[a_col]["links"])})
        if len(rows) > len(best):
            best = rows
        elif not rows:
            last_problem = "a table with the right headings but no rows that could be read"
    if not best:
        raise ValueError(last_problem)
    return best

# ---------------------------------------------------------------- the charts

def load_catalog():
    try:
        with open(CHARTS_FILE, encoding="utf-8") as f:
            return json.load(f)["charts"]
    except (OSError, ValueError, KeyError) as e:
        die("Cannot read the chart catalog %s: %s" % (CHARTS_FILE, e), 9)


def find_chart(ref, catalog):
    want = ref.strip().casefold()
    if not want:
        die("A chart name is empty.")
    hits = [c for c in catalog if want == c["id"] or want == c["name"].casefold() or want in [a.casefold() for a in c["aliases"]]]
    if not hits:
        hits = [c for c in catalog if want in c["name"].casefold()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(json.dumps({"unknown_chart": ref, "built_in": [{"id": c["id"], "name": c["name"]} for c in catalog]}, ensure_ascii=False))
        die("No built-in chart matches %r. Use `charts` to list them, or give a Wikipedia list with --custom-page." % ref, 7)
    print(json.dumps({"ambiguous": ref, "candidates": [{"id": c["id"], "name": c["name"]} for c in hits]}, ensure_ascii=False))
    die("%d charts match %r." % (len(hits), ref), 6)


class Chart:
    """A chart and the Wikipedia pages that hold its number ones."""

    def __init__(self, cid, name, date_meaning, pages=None, custom_pages=None):
        self.id, self.name, self.date_meaning = cid, name, date_meaning
        self.ranges = pages or []
        self.custom = custom_pages
        self._rows = None

    def page_for(self, year):
        """(title, year_hint) of the page that holds the number ones of a year, or None."""
        for p in self.ranges:
            if p["from"] <= year <= p["to"]:
                return p["title"].replace("{year}", str(year)).replace("{decade}", str(year // 10 * 10)), year
        return None

    def first_year(self):
        return min(p["from"] for p in self.ranges) if self.ranges else None

    def rows_for(self, year):
        """The rows of the page that holds a year."""
        if self.custom is not None:
            if self._rows is None:
                self._rows = []
                for title, hint in self.custom:
                    self._rows += self._read(title, hint)
                self._rows.sort(key=lambda r: r["start"])
            return self._rows
        p = self.page_for(year)
        return self._read(*p) if p else []

    def rows_around(self, day):
        """Rows from the pages that can hold the number one on this day: its year, and a neighbouring year only when needed
        (the day is before the first row of its year, or close enough to the year's end that a week-ending date of the next
        year can already cover it)."""
        rows = list(self.rows_for(day.year))
        if self.custom is not None:
            return rows
        if self.date_meaning == "week_ending" and (day + datetime.timedelta(days=7)).year > day.year:
            rows += self.rows_for(day.year + 1)
        if not any(r["start"] <= day for r in rows):
            rows += self.rows_for(day.year - 1)
        rows.sort(key=lambda r: r["start"])
        return rows

    def _read(self, title, hint):
        key = (title, hint, self.date_meaning)
        if key not in _PARSED:
            _PARSED[key] = self._read_uncached(title, hint)
        return _PARSED[key]

    def _read_uncached(self, title, hint):
        text = fetch_page(title)
        if text is None:
            return []
        try:
            rows = read_numberones(text, hint)
        except ValueError as e:
            die("The Wikipedia page %r no longer looks like a list of number ones (%s). The layout may have changed; "
                "nothing is guessed." % (title, e), 6)
        shift = datetime.timedelta(days=-6 if self.date_meaning == "week_ending" else 0)
        return [dict(r, start=r["date"] + shift) for r in rows]

    def number_one_on(self, day):
        """The row that was number one on this day, or (None, reason)."""
        if self.custom is None and self.page_for(day.year) is None:
            return None, "before_chart" if self.first_year() and day.year < self.first_year() else "no_data"
        rows = self.rows_around(day)
        if not rows:
            return None, "no_data"
        before = [r for r in rows if r["start"] <= day]
        if not before:
            return None, "before_chart"
        chosen = before[-1]
        if chosen.get("no_chart"):
            return None, "no_chart"
        later = [r for r in rows if r["start"] > chosen["start"]]
        if not later and (day - chosen["start"]).days > 200:
            return None, "no_data"
        return chosen, None

# ---------------------------------------------------------------- talking to spotify_cli

def spotify_cli():
    path = os.environ.get("SPOTIFY_CLI_PATH")
    if path:
        return path
    if sys.platform == "darwin":
        return "/Applications/Spotify.app/Contents/MacOS/spotify_cli"
    return "spotify_cli.exe" if os.name == "nt" else "spotify_cli"


class CliError(Exception):
    pass


def cli(*args):
    try:
        r = subprocess.run([spotify_cli()] + list(args), capture_output=True, text=True)
    except FileNotFoundError:
        die("spotify_cli was not found. Install the Spotify desktop app, or set SPOTIFY_CLI_PATH.", 3)
    if r.returncode != 0:
        raise CliError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def cli_json(*args):
    try:
        out = cli(*args, "--format", "json")
    except CliError:
        time.sleep(0.5)  # the app sometimes drops a connection when it is busy; a read is safe to try once more
        out = cli(*args, "--format", "json")
    try:
        return json.loads(out)
    except ValueError:
        raise CliError("unexpected answer: " + out.strip()[:200])


# ---------------------------------------------------------------- matching a chart song to a Spotify track

def norm(s):
    s = re.sub("[\u2018\u2019\u02bc'`]", "", str(s))  # don't, don’t and dont are the same word
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


_FEAT = re.compile(r"\s*[\(\[]\s*(?:feat\.?|featuring|ft\.?|with)\b[^\)\]]*[\)\]]", re.I)
_BAD = re.compile(r"\b(live|remix|mix|karaoke|instrumental|cover|version|acoustic|demo|edit|tribute|session|sessions|reprise|rework|"
                  r"re-?record\w*|sped|slowed|nightcore|orchestral|unplugged|a cappella|radio|extended|dub|bootleg|medley|mashup)\b", re.I)
_GOOD_VERSION = re.compile(r"\b(single|album|original|stereo|mono) version\b", re.I)
_REMASTER = re.compile(r"\b(remaster\w*|deluxe|anniversary|expanded|digital)\b", re.I)
_FROM = re.compile(r"^\s*from\b", re.I)


def split_title(t):
    """(whole title without feat., main part, qualifiers) of a track title such as 'Song - 2011 Remaster' or 'Song (Live)'."""
    t0 = _FEAT.sub("", t).strip()
    t0 = re.sub(r"\s+(?:feat\.?|featuring|ft\.?)\s+.*$", "", t0, flags=re.I)
    parts = re.split(r"\s+-\s+|\s*[\(\[]", t0, maxsplit=1)
    main = parts[0]
    return t0, main, t0[len(main):].strip(" -([])") if len(parts) > 1 else ""


def title_rank(spotify_title, chart_title):
    """0 = the same title, 1 = the same song with a harmless note (remaster, 'from ...'), 2 = some other note, None = no match."""
    s_full, s_main, s_qual = split_title(spotify_title)
    c_full, c_main, c_qual = split_title(chart_title)
    if norm(s_full) == norm(c_full):
        return 0
    if norm(s_main) != norm(c_main):
        return None
    if _GOOD_VERSION.search(s_qual):
        return 1
    for bad in _BAD.findall(s_qual):
        word = bad if isinstance(bad, str) else bad[0]
        if not re.search(r"\b" + re.escape(word) + r"\b", c_qual, re.I):
            return None  # the chart's own note (not its title) has to carry the same word
    if _REMASTER.search(s_qual) or _FROM.match(s_qual):
        return 1
    return 2


_JOINERS = ("and", "with", "featuring", "feat", "ft", "vs", "x")


def artist_matches(chart_artist, spotify_artists):
    """Strict where it matters: the first artist agrees, and every artist the chart names is credited on the Spotify track,
    so a solo version never stands in for a duet. The track may credit more artists than the chart (a band member, a guest)."""
    a = norm(chart_artist)
    names = [norm(x) for x in spotify_artists if norm(x)]
    if not a or not names:
        return False
    if a == names[0]:
        return True
    if not a.startswith(names[0] + " "):
        return False
    rest = " " + a + " "
    for n in sorted(set(names), key=len, reverse=True):
        rest = rest.replace(" " + n + " ", " ")
    return not [w for w in rest.split() if w not in _JOINERS]


_FIRST_ARTIST = re.compile(r"\s+\(?(?:featuring|feat\.?|ft\.?|with|and|&|vs\.?|x)\s+|\s*,\s*", re.I)


def search_queries(artist, title):
    """The searches to try for one chart row, most specific first: the whole artist, the first artist, the title alone."""
    first = _FIRST_ARTIST.split(artist, maxsplit=1)[0]
    queries = []
    for q in ("%s %s" % (artist, title), "%s %s" % (first, title), title):
        q = clean_text(q.lstrip("- "), 200)
        if q and q not in queries:
            queries.append(q)
    return queries


def _search_as(artist, titles):
    """Search for a chart song under one spelling of the artist. Returns (track or None, other_versions, near)."""
    other_versions, near = False, None
    for title in titles[:2]:
        for query in search_queries(artist, title):
            try:
                tracks = cli_json("search", query, "--type", "track", "--limit", "10").get("tracks", [])
            except CliError as e:
                raise CliError(clean_text(str(e), 120))
            best = None
            for i, t in enumerate(tracks):
                if not isinstance(t, dict) or not TRACK_URI.fullmatch(str(t.get("uri", ""))):
                    continue
                name, artists = clean_text(t.get("name", "")), [clean_text(a) for a in t.get("artists", [])]
                if not artist_matches(artist, artists):
                    if near is None and artists and norm(artists[0]) and norm(artist).startswith(norm(artists[0])) \
                            and title_rank(name, title) is not None:
                        near = "%s - %s" % (", ".join(artists), name)  # the same song, credited to other artists
                    continue
                rank = title_rank(name, title)
                if rank is None:
                    if norm(split_title(name)[1]) == norm(split_title(title)[1]):
                        other_versions = True
                    continue
                if best is None or (rank, i) < best[0]:
                    best = ((rank, i), {"uri": t["uri"], "name": name, "artists": artists})
            if best:
                return best[1], other_versions, near
    return None, other_versions, near


def search_track(row):
    """The best Spotify track for a chart row: (track dict or None, reason). The match itself is strict: the title and
    the artist have to agree with the chart, whatever the search returns. An artist who has changed their name (Wikipedia's
    article for them has the new name) is tried under both names; the track then says which name found it ('via')."""
    other_versions, near = False, None
    for artist in [row["artist"]] + ([row["artist_now"]] if row.get("artist_now") else []):
        try:
            track, other, close = _search_as(artist, row["titles"])
        except CliError as e:
            return None, "search failed: %s" % e
        if track:
            if artist != row["artist"]:
                track["via"] = artist
            return track, None
        other_versions, near = other_versions or other, near or close
    if near:
        return None, "not matched: found %r, but its artist credits differ from the chart's" % near
    return None, "only live, remix or other versions found" if other_versions else "not found on Spotify"

# ---------------------------------------------------------------- who was born or died, and which albums came out

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MAX_PEOPLE = 60  # musicians looked up per angle (born, died)
MAX_ALBUMS = 60
MAX_HITS_PER_WEEK = 3  # a weekly chart starts at most two number ones in 7 days; a page with more is broken or vandalised
MAX_ALBUMS_PER_YEAR = 15  # the lists are long for recent years and not ordered by fame

_MUSIC = re.compile(r"\b(singers?|songwriters?|singer-songwriters?|musicians?|composers?|guitarists?|drummers?|bassists?|pianists?|"
                    r"violinists?|cellists?|saxophonists?|trumpeters?|trombonists?|clarinet\w*|rappers?|disc jockeys?|DJs?|conductors?|"
                    r"band ?leaders?|vocalists?|organists?|jazz|opera|tenors?|sopranos?|baritones?|mezzo-sopranos?|record producers?|"
                    r"flautists?|flutists?|harpists?|accordionists?|percussionists?|keyboardists?|lyricists?|MCs?|DJ)\b", re.I)


class _Items(HTMLParser):
    """The list items of a part of a Wikipedia page, with the articles they link to."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.cur, self.skip, self.link = [], None, 0, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li":
            self.cur = {"text": "", "links": []}
        elif self.cur is None:
            return
        elif tag in ("sup", "style", "script"):
            self.skip += 1
        elif tag == "a" and not self.skip and str(a.get("href", "")).startswith("/wiki/") and a.get("title"):
            self.link = {"title": a["title"], "text": ""}

    def handle_endtag(self, tag):
        if tag == "li" and self.cur is not None:
            self.items.append(self.cur)
            self.cur = None
        elif tag == "a" and self.link is not None:
            if self.cur is not None and self.link["text"].strip():
                self.cur["links"].append((self.link["text"].strip(), self.link["title"]))
            self.link = None
        elif tag in ("sup", "style", "script") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.cur is not None and not self.skip:
            self.cur["text"] += data
            if self.link is not None:
                self.link["text"] += data


def day_title(day):
    return "%s %d" % (MONTH_NAMES[day.month - 1], day.day)


def _part(text, start_id, end_ids):
    i = text.find('id="%s"' % start_id)
    if i < 0:
        return None
    ends = [text.find('id="%s"' % e, i + 1) for e in end_ids]
    ends = [e for e in ends if e > 0]
    return text[i:min(ends) if ends else len(text)]


def read_people(html_text):
    """{'born': [...], 'died': [...]} from a day page such as 'October 23': musicians only (the one-line description of
    each person decides). Each is {'year', 'name', 'description', 'alias'}."""
    out = {}
    for key, sec, ends in (("born", "Births", ["Deaths", "Holidays_and_observances"]), ("died", "Deaths", ["Holidays_and_observances", "References"])):
        part = _part(html_text, sec, ends)
        if part is None:
            raise ValueError("no %s section" % sec)
        p = _Items()
        p.feed(part)
        people = []
        for it in p.items:
            text = clean_cell(it["text"])
            m = re.match(r"^(\d{3,4})(?:\s*\([^)]*\))?\s*[–—-]\s*(.+)$", text)
            if not m:
                continue
            year, rest = int(m.group(1)), m.group(2)
            names = [l for l in it["links"] if not re.fullmatch(r"\d{1,4}( BC)?", l[0])]
            if not names or "," not in rest:
                continue
            name = clean_text(names[0][0], 100)
            desc = rest.split(",", 1)[1]
            desc = clean_text(re.sub(r"\s*\((?:born|died|b\.|d\.)[^)]*\)\s*$", "", desc), 160)
            if not name or not _MUSIC.search(desc):
                continue
            title = re.sub(r"\s*\([^)]*\)\s*$", "", names[0][1])
            alias = clean_text(title, 100) if norm(title) != norm(name) and norm(title) else None
            people.append({"year": year, "name": name, "description": desc, "alias": alias})
        if not p.items:
            raise ValueError("the %s section has no list" % sec)
        out[key] = people
    return out


def _blocks(text):
    """A page cut at its headings: [(heading id, html)], so a table knows which month's heading it sits under."""
    marks = [(m.start(), m.group(1), m.group(2)) for m in re.finditer(r'<h([1-6])[^>]*\bid="([^"]+)"', text)]
    return [(mid, int(level), text[pos:(marks[i + 1][0] if i + 1 < len(marks) else len(text))]) for i, (pos, level, mid) in enumerate(marks)]


def _month_of(heading):
    name = re.sub(r"_\d+$", "", heading)
    return MONTH_NAMES.index(name) + 1 if name in MONTH_NAMES else None


def _day_and_month(texts, hint):
    """(month, day) from the date cells of a row: '4' under a month heading, 'October / 4', or a month written as stacked letters
    ('O / C / T / O / B / E / R') in front of the day."""
    joined = " ".join(texts)
    month = next((i + 1 for i, n in enumerate(MONTH_NAMES) if re.search(n, joined, re.I)), None)
    if month is None and len(texts) > 1:
        letters = re.sub(r"[^A-Za-z]", "", texts[0]).upper()
        month = next((i + 1 for i, n in enumerate(MONTH_NAMES) if n.upper() == letters), None)
    nums = re.findall(r"(?<!\d)(\d{1,2})(?!\d)", re.sub(r"\b(?:19|20)\d\d\b", " ", joined))
    return (month or hint), (int(nums[-1]) if nums else None)


def read_album_tables(html_text, only=None):
    """[{'month', 'day', 'album', 'artist'}] from the album tables of a Wikipedia page. The month comes from the heading above the
    table, from 'October / 4' or from a month column. `only` limits the page to one section of it (by heading id)."""
    blocks = _blocks(html_text)
    if only is not None:
        start = next((i for i, b in enumerate(blocks) if b[0] == only), None)
        if start is None:
            return None
        end = next((j for j in range(start + 1, len(blocks)) if blocks[j][1] <= blocks[start][1]), len(blocks))
        blocks = blocks[start:end]
    rows, found = [], False
    for heading, _, html_part in blocks:
        if "<table" not in html_part:
            continue
        parser = _Tables()
        parser.feed(html_part)
        for table in parser.tables:
            if "wikitable" not in table["cls"]:
                continue
            try:
                grid = expand(table["rows"])
            except ValueError:
                continue
            header = next((r for r in grid if len(r) >= 3 and all(c["tag"] == "th" for c in r) and len({id(c) for c in r}) >= 3), None)
            if header is None:
                continue
            names = [" ".join(c["text"].lower().split()) for c in header]
            a_col = next((i for i, n in enumerate(names) if n.startswith("album") or n.startswith("title")), None)
            r_col = next((i for i, n in enumerate(names) if n.startswith("artist")), None)
            d_cols = [i for i, n in enumerate(names) if n.startswith("day") or "date" in n]
            if a_col is None or r_col is None or not d_cols:
                continue
            found = True
            for r in grid[grid.index(header) + 1:]:
                if len(r) <= max(a_col, r_col, *d_cols):
                    continue
                month, day = _day_and_month([r[i]["text"] for i in d_cols], _month_of(heading))
                album = clean_cell(r[a_col]["text"])
                artist = " ".join(clean_cell(r[r_col]["text"]).replace(" / ", " ").split())
                if month and day and album and artist:
                    rows.append({"month": month, "day": day, "album": album, "artist": artist})
    return rows if found else None


def album_rows(year, month):
    """The albums of a year as Wikipedia lists them, or None if no list could be read. Newer years have a page of their own
    ('List of 2016 albums', for the newest ones in two halves); older years list them on '<year> in music'."""
    half = "January\u2013June" if month <= 6 else "July\u2013December"
    for title, only in (("List of %d albums" % year, None), ("List of %d albums (%s)" % (year, half), None), ("%d in music" % year, "Albums_released")):
        text = fetch_page(title)
        if text is None:
            continue
        rows = read_album_tables(text, only)
        if rows:
            return rows
    return None


# ---------------------------------------------------------------- finding them on Spotify

def lookup(uris):
    """{uri: {'name', 'artists': [(name, uri)], 'album', 'album_uri', 'plays'}} for tracks, with their play counts."""
    out = {}
    uris = [u for u in uris if TRACK_URI.fullmatch(str(u))]
    for i in range(0, len(uris), 20):
        data = cli_json("lookup", *uris[i:i + 20], "--fields", "total_plays,release_date")
        for e in data.get("entities", []) if isinstance(data, dict) else []:
            if not isinstance(e, dict) or not TRACK_URI.fullmatch(str(e.get("uri", ""))):
                continue
            meta = {m.get("field"): m.get("value") for m in e.get("metadata", []) if isinstance(m, dict)}
            try:
                plays = int(meta.get("total_plays") or 0)
            except ValueError:
                plays = 0
            parent = e.get("parent") if isinstance(e.get("parent"), dict) else {}
            out[e["uri"]] = {"name": clean_text(e.get("name", "")),
                             "artists": [(clean_text(c.get("name", "")), str(c.get("uri", ""))) for c in e.get("contributors", []) if isinstance(c, dict)],
                             "album": clean_text(parent.get("name", "")), "album_uri": str(parent.get("uri", "")), "plays": plays}
    return out


def search_tracks(query, limit=20):
    data = cli_json("search", query, "--type", "track", "--limit", str(limit))
    return [t["uri"] for t in data.get("tracks", []) if isinstance(t, dict) and TRACK_URI.fullmatch(str(t.get("uri", "")))]


def quote(s):
    return clean_text(s, 120).replace('"', " ").replace("\\", " ")


def is_plain_version(title):
    """False for a live, remix, karaoke... version of a song (those are never picked as 'the' song)."""
    return not _BAD.search(split_title(title)[2])


def top_tracks(infos, keep=8):
    """The most played tracks, one per song title, studio versions first."""
    ranked = sorted(infos, key=lambda x: -x["plays"])
    plain = [x for x in ranked if is_plain_version(x["name"])] or ranked
    seen, out = set(), []
    for x in plain:
        key = norm(split_title(x["name"])[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) == keep:
            break
    return out


def track_entry(uri, x):
    return {"uri": uri, "name": x["name"], "artists": [a[0] for a in x["artists"]], "plays": x["plays"]}


def find_artist(name, alias=None):
    """(artist_uri, tracks, None) or (None, [], reason). The Spotify artist's name has to be the name from Wikipedia (or the
    name of its article); if several artists have it, one has to be far ahead of the others in plays."""
    wanted = {norm(name)} | ({norm(alias)} if alias else set())
    try:
        uris = search_tracks('artist:"%s"' % quote(name))
        info = lookup([u for u in uris])
    except CliError as e:
        return None, [], "search failed: %s" % e
    by_artist = {}
    for u, x in info.items():
        if x["artists"] and norm(x["artists"][0][0]) in wanted:
            by_artist.setdefault(x["artists"][0][1], []).append((u, x))
    if not by_artist:
        return None, [], "not found on Spotify under this name"
    best = sorted(by_artist.items(), key=lambda kv: -max(x["plays"] for _, x in kv[1]))
    if len(best) > 1:
        a, b = max(x["plays"] for _, x in best[0][1]), max(x["plays"] for _, x in best[1][1])
        if b * 10 > a:
            return None, [], "unsure: %d Spotify artists have this name and none is clearly the one" % len(best)
    uri, items = best[0]
    top = top_tracks([x | {"_uri": u} for u, x in items])
    return uri, [track_entry(x["_uri"], x) for x in top], None


def base_title(s):
    s = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", s.strip())
    return norm(s)


def find_album(album, artist):
    """(album_uri, tracks, None) or (None, [], reason): the tracks of an album found by its name and artist."""
    first = _FIRST_ARTIST.split(artist, maxsplit=1)[0]
    want = {base_title(album), base_title(re.sub(r"\s+EP$", "", album))}
    try:
        uris = search_tracks('artist:"%s" album:"%s"' % (quote(first), quote(album)))
        info = lookup(uris)
    except CliError as e:
        return None, [], "search failed: %s" % e
    group = {}
    for u, x in info.items():
        if base_title(x["album"]) in want and artist_matches(artist, [a[0] for a in x["artists"]]):
            group.setdefault(x["album_uri"], []).append((u, x))
    if not group:
        return None, [], "not found on Spotify"
    album_uri, items = max(group.items(), key=lambda kv: max(x["plays"] for _, x in kv[1]))
    top = top_tracks([x | {"_uri": u} for u, x in items])
    return album_uri, [track_entry(x["_uri"], x) for x in top], None


# ---------------------------------------------------------------- discover: everything that happened on a date

def round_years(now, wanted):
    """Years whose album anniversary counts: multiples of 5 (at least 5), plus this year and last year."""
    if wanted == "all":
        return list(range(1950, now.year + 1))
    years = {y for y in range(1950, now.year + 1) if (now.year - y) % 5 == 0 and now.year - y >= 5}
    return sorted(years | {now.year, now.year - 1})


def ago(now, year):
    n = now.year - year
    return "this year" if n == 0 else ("last year" if n == 1 else "%d years ago" % n)


def cmd_charts(args):
    print(json.dumps([{"id": c["id"], "name": c["name"], "first_year": min(p["from"] for p in c["pages"])} for c in load_catalog()], indent=2))


def discover_run(args, pool):
    now = today()
    if args.date:
        try:
            day = datetime.date.fromisoformat(args.date)
        except ValueError:
            die("--date must be a real date as YYYY-MM-DD.")
    else:
        day = now
    # the year of the date does not matter for births, deaths and albums; only the month and day do
    month, dom = day.month, day.day
    items, skipped = [], []
    tasks, progress, lock = [], [0], threading.Lock()

    def submit(meta, fn, *a):
        """Start a Spotify lookup in the background; the results are collected in the order they were started."""
        fut = pool.submit(fn, *a)

        def finished(_f):
            with lock:
                progress[0] += 1
                print("spotify: %d of %d looked up" % (progress[0], len(tasks)), file=sys.stderr)
        tasks.append((meta, fut))
        fut.add_done_callback(finished)

    def add(**kw):
        kw["id"] = len(items) + 1
        items.append(kw)

    # births and deaths
    page = fetch_page(day_title(day))
    if page is None:
        die("Wikipedia has no page %r." % day_title(day), 6)
    try:
        people = read_people(page)
    except ValueError as e:
        die("The Wikipedia page %r no longer looks like a day page (%s). The layout may have changed; nothing is guessed." % (day_title(day), e), 6)
    for key in ("born", "died"):
        todo = [p for p in people[key] if p["year"] <= now.year]
        if len(todo) > MAX_PEOPLE:
            skipped.append("%s: only the first %d of %d musicians were looked up" % (key, MAX_PEOPLE, len(todo)))
            todo = todo[:MAX_PEOPLE]
        for p in todo:
            reason = ("born %d, %s" if key == "born" else "died %d, %s") % (p["year"], ago(now, p["year"]))
            submit({"kind": "person", "angle": key, "year": p["year"], "label": p["name"], "detail": p["description"], "reason": reason},
                   find_artist, p["name"], p["alias"])

    # album anniversaries
    years = round_years(now, args.album_years)
    albums = []
    for y in years:
        rows = album_rows(y, month)
        if rows is None:
            skipped.append("albums %d: no list of albums for %s on Wikipedia" % (y, MONTH_NAMES[month - 1]))
            continue
        todo = [r for r in rows if r["month"] == month and r["day"] == dom and not (y == now.year and datetime.date(y, month, min(dom, 28)) > now)]
        if len(todo) > MAX_ALBUMS_PER_YEAR:
            skipped.append("albums %d: only the first %d of %d were looked up" % (y, MAX_ALBUMS_PER_YEAR, len(todo)))
            todo = todo[:MAX_ALBUMS_PER_YEAR]
        albums += [dict(r, year=y) for r in todo]
    if len(albums) > MAX_ALBUMS:
        skipped.append("albums: only the first %d of %d were looked up" % (MAX_ALBUMS, len(albums)))
        albums = albums[:MAX_ALBUMS]
    for a in albums:
        submit({"kind": "album", "angle": "album", "year": a["year"], "label": "%s - %s" % (a["artist"], a["album"]), "detail": "album",
                "reason": "released %d, %s" % (a["year"], ago(now, a["year"]))}, find_album, a["album"], a["artist"])

    # number ones that reached the top in the week up to this date
    catalog = load_catalog()
    charts = [Chart(c["id"], c["name"], c["date_meaning"], pages=c["pages"]) for c in catalog if not args.chart or c["id"] in args.chart or c["name"] in args.chart]
    if args.chart and not charts:
        die("No built-in chart matches %s. Use `charts`." % args.chart, 7)
    hits = []
    for chart in charts:
        first = chart.first_year()
        for n, y in enumerate(range(first, day.year + 1), 1):
            if n % 10 == 0:
                print("%s: %d" % (chart.name[:30], y), file=sys.stderr)
            try:
                d = datetime.date(y, month, dom)
            except ValueError:
                continue
            if d > now:
                continue
            lo = d - datetime.timedelta(days=6)
            window = [r for r in chart.rows_around(d) if lo <= r["start"] <= d and not r.get("no_chart")]
            if len(window) > MAX_HITS_PER_WEEK:
                skipped.append("%s %d: only the first %d of %d number ones in one week were looked up" % (chart.name, y, MAX_HITS_PER_WEEK, len(window)))
                window = window[:MAX_HITS_PER_WEEK]
            hits += [(chart, y, r) for r in window]
    seen = set()
    for chart, y, r in hits:
        key = (chart.id, r["start"], norm(r["artist"]))
        if key in seen:
            continue
        if len(seen) >= args.max_number_ones:
            skipped.append("number ones: only the first %d of %d were looked up" % (args.max_number_ones, len({(c.id, h["start"], norm(h["artist"])) for c, _, h in hits})))
            break
        seen.add(key)
        submit({"kind": "number_one", "angle": "number_one", "year": y, "label": "%s - %s" % (r["artist"], " / ".join(r["titles"])), "detail": chart.name,
                "reason": "number one in %s from %s, %s" % (chart.name, r["start"].isoformat(), ago(now, y))}, resolve_number_one, r)

    # collect what the lookups found, in the order they were started
    for meta, fut in tasks:
        res = fut.result()
        base = {k: meta[k] for k in ("angle", "year", "label", "detail", "reason")}
        if meta["kind"] == "person":
            uri, tracks, why = res
            add(**base, status="matched" if uri else ("unsure" if why.startswith("unsure") else "not_found"), why=why, artist_uri=uri,
                tracks=tracks, plays=tracks[0]["plays"] if tracks else 0)
        elif meta["kind"] == "album":
            uri, tracks, why = res
            add(**base, status="matched" if uri else "not_found", why=why, album_uri=uri, tracks=tracks, plays=tracks[0]["plays"] if tracks else 0)
        else:
            track, why, tracks = res
            add(**base, status="matched" if track else "not_found", why=why, tracks=tracks, plays=tracks[0]["plays"] if tracks else 0)

    sel = {"version": 1, "date_label": "%d %s" % (dom, MONTH_NAMES[month - 1]), "month_day": "%02d-%02d" % (month, dom), "items": items, "skipped": skipped}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), mode=0o700, exist_ok=True)
    write_private(args.out, sel)
    counts = {}
    for it in items:
        k = "%s/%s" % (it["angle"], it["status"])
        counts[k] = counts.get(k, 0) + 1
    def row(it):
        return {"id": it["id"], "angle": it["angle"], "label": it["label"], "detail": it["detail"], "reason": it["reason"], "status": it["status"],
                "why": it["why"], "top_track": (it["tracks"][0]["artists"][0] + " - " + it["tracks"][0]["name"]) if it["tracks"] else None, "plays": it["plays"]}
    shown, hidden, hidden_unmatched = [], {}, {}
    for angle in ANGLES:
        group = [it for it in items if it["angle"] == angle]
        good = sorted([it for it in group if it["status"] == "matched"], key=lambda it: -it["plays"])
        rest = [it for it in group if it["status"] != "matched"]
        top = good if args.show == 0 else good[:args.show]
        rest_shown = rest if args.show == 0 else rest[:args.show]
        shown += top + rest_shown
        if len(good) > len(top):
            hidden[angle] = len(good) - len(top)
        if len(rest) > len(rest_shown):
            hidden_unmatched[angle] = len(rest) - len(rest_shown)
    print(json.dumps({"date": sel["date_label"], "candidates_file": os.path.abspath(args.out), "counts": counts, "skipped": skipped,
                      "not_shown_most_played_first": hidden, "unmatched_not_shown": hidden_unmatched, "items": [row(it) for it in shown]}, indent=2, ensure_ascii=False))


def resolve_number_one(r):
    track, why = search_track(r)
    info = lookup([track["uri"]]).get(track["uri"]) if track else None
    tracks = [track_entry(track["uri"], info)] if info else ([{"uri": track["uri"], "name": track["name"], "artists": track["artists"], "plays": 0}] if track else [])
    return track, why, tracks


def cmd_discover(args):
    if not 1 <= args.workers <= 8:
        die("--workers must be between 1 and 8.")
    if not 1 <= args.max_number_ones <= 2000:
        die("--max-number-ones must be between 1 and 2000.")
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        discover_run(args, pool)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # on an error, do not wait for the lookups still queued


ANGLES = ["born", "died", "album", "number_one"]


# ---------------------------------------------------------------- compose: choose what goes into the list


def confirm_code(uris, extra=""):
    return hashlib.sha256(json.dumps({"uris": uris, "extra": extra}).encode("utf-8")).hexdigest()[:12]


def write_private(path, data):
    if os.path.islink(path):
        die("%s is a symbolic link; refusing to write through it." % path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_candidates(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data["items"]
        assert isinstance(items, list)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        die("Cannot read the candidates file: %s" % e)
    for it in items:
        for t in it.get("tracks", []):
            if not TRACK_URI.fullmatch(str(t.get("uri", ""))):
                die("The candidates file contains an invalid track URI.")
    return data


def song_key(track):
    return norm(split_title(track["name"])[1]) + "|" + norm(track["artists"][0] if track["artists"] else "")


def auto_pick(items, count, per_angle, angles):
    """About `count` items, mixed: round by round, one item from each angle (the most played first) until every angle has given
    `per_angle`; then the angles that still have items fill what is left. A song that is already in the list is skipped."""
    pools = {a: sorted([i for i in items if i["angle"] == a and i["status"] == "matched" and i["tracks"]], key=lambda i: -i["plays"]) for a in angles}
    out, seen, given = [], set(), {a: 0 for a in angles}

    def take(a):
        while pools[a]:
            it = pools[a].pop(0)
            keys = {it["tracks"][0]["uri"], song_key(it["tracks"][0])}
            if not keys & seen:
                seen.update(keys)
                given[a] += 1
                out.append(it)
                return True
        return False

    for limit in (per_angle, count):
        progress = True
        while len(out) < count and progress:
            progress = False
            for a in angles:
                if len(out) < count and given[a] < limit and take(a):
                    progress = True
    return out


def cmd_compose(args):
    data = load_candidates(args.candidates)
    items = {i["id"]: i for i in data["items"]}
    picks = []  # (item, how many tracks | 'album')
    if args.auto:
        angles = [a for a in ANGLES if not args.angles or a in args.angles]
        chosen = auto_pick(list(items.values()), args.count, args.per_angle, angles)
        picks = [(i, 1) for i in chosen]
        order = list(picks)
        random.Random(args.seed).shuffle(order)
        picks = order
    else:
        for spec in args.item or []:
            m = re.fullmatch(r"(\d+)(?::(\d+|album))?", spec.strip())
            if not m:
                die("--item must look like 7, 7:3 (the 3 most played songs) or 14:album, not %r." % spec)
            iid = int(m.group(1))
            if iid not in items:
                die("There is no item %d." % iid)
            how = m.group(2) or "1"
            picks.append((items[iid], "album" if how == "album" else int(how)))
        if args.shuffle:
            random.Random(args.seed).shuffle(picks)
    if not picks:
        die("Nothing was chosen. Use --auto or --item.")
    tracks, seen, notes = [], set(), []
    for it, how in picks:
        if it["status"] != "matched" or not it["tracks"]:
            notes.append("item %d (%s) cannot be used: %s" % (it["id"], it["label"], it.get("why") or it["status"]))
            continue
        if how == "album":
            if it["angle"] != "album" or not re.fullmatch(r"spotify:album:[A-Za-z0-9]+", str(it.get("album_uri"))):
                notes.append("item %d is not an album; its most played song is used" % it["id"])
                how = 1
            else:
                try:
                    full = cli_json("playlist", "get", it["album_uri"])
                except CliError as e:
                    die("Could not read the album %s: %s" % (it["label"], e), 8)
                entries = [t for t in full.get("tracks", []) if isinstance(t, dict) and TRACK_URI.fullmatch(str(t.get("uri", "")))]
                if not entries or len(entries) > 120:
                    die("The album %s has %d readable tracks; not adding it." % (it["label"], len(entries)), 4)
                for t in entries:
                    if t["uri"] not in seen:
                        seen.add(t["uri"])
                        tracks.append({"uri": t["uri"], "name": clean_text(t.get("name", "")), "artists": [clean_text(a) for a in t.get("artists", [])],
                                       "item": it["id"], "reason": "whole album: " + it["reason"]})
                continue
        for t in it["tracks"][:how]:
            key = song_key(t)
            if t["uri"] in seen or key in seen:
                continue
            seen.add(t["uri"])
            seen.add(key)
            tracks.append({"uri": t["uri"], "name": t["name"], "artists": t["artists"], "item": it["id"], "reason": it["label"] + ": " + it["reason"]})
    if not tracks:
        die("None of the chosen items can be used: %s" % ("; ".join(notes) or "nothing was chosen"), 1)
    uris = [t["uri"] for t in tracks]
    code = confirm_code(uris)
    sel = {"version": 1, "confirm_code": code, "confirm_code_public": confirm_code(uris, "public"), "date_label": data.get("date_label", ""), "month_day": data.get("month_day", ""), "tracks": tracks}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), mode=0o700, exist_ok=True)
    write_private(args.out, sel)
    print(json.dumps({"tracks": len(tracks), "confirm_code": code, "confirm_code_public": confirm_code(uris, "public"), "selection_file": os.path.abspath(args.out), "notes": notes,
                      "list": [{"n": i + 1, "song": "%s - %s" % (", ".join(t["artists"]), t["name"]), "why": t["reason"]} for i, t in enumerate(tracks)]},
                     indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- changing the user's Spotify (as in playlist-vennify)

def load_selection(path, confirm, limit, what, extra=""):
    try:
        with open(path, encoding="utf-8") as f:
            sel = json.load(f)
    except (OSError, ValueError) as e:
        die("Cannot read the selection file: %s" % e)
    uris = [t.get("uri", "") for t in sel.get("tracks", [])] if isinstance(sel, dict) else []
    for u in uris:
        if not TRACK_URI.fullmatch(str(u)):
            die("The selection contains an invalid track URI: %r" % u)
    if len(set(uris)) != len(uris):
        die("The selection contains a track twice.")
    if not uris:
        die("The selection is empty: there is nothing to %s." % what)
    if confirm != confirm_code(uris, extra):
        die("The confirmation code does not match this list%s. Run `compose` again, show the user the list, "
            "ask again, and use the code from that run." % (" and visibility" if extra else ""), 2)
    if len(uris) > limit:
        die("%d tracks is over the limit of %d for this action. Tell the user, and raise --max-tracks only if they agree." % (len(uris), limit), 4)
    return sel, uris


def queue_entries():
    return cli_json("queue", "list").get("next_tracks", [])


def cmd_queue(args):
    sel, uris = load_selection(args.selection, args.confirm, args.max_tracks, "queue")
    before = queue_entries()
    mine = sum(1 for e in before if e.get("provider") == "queue")
    if args.dry_run:
        print(json.dumps({"would_add": len(uris), "position": args.position, "already_in_queue": mine}))
        return
    added = 0
    try:
        for u in uris:
            cli("queue", "add", u)
            added += 1
    except CliError as e:
        print("error: the queue stopped accepting tracks after %d of %d: %s" % (added, len(uris), e), file=sys.stderr)
        print(json.dumps({"added": added, "requested": len(uris)}))
        sys.exit(8)
    moved = False
    if args.position == "front" and mine > 0:
        after = queue_entries()
        if [e.get("uri") for e in after[mine:mine + added]] == uris[:added]:
            for i in range(added):
                cli("queue", "move", str(mine + i), str(i))
            moved = True
        else:
            print("warning: the queue did not look as expected, so the tracks stay at the end of it.", file=sys.stderr)
    elif args.position == "front":
        moved = True
    print(json.dumps({"added": added, "position": "front" if moved else "end", "your_own_queued_tracks_before": mine}))


def run_batch(ops):
    d = tempfile.mkdtemp(prefix="on-this-day-")
    path = os.path.join(d, "batch.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ops": ops}, f, ensure_ascii=False)
        out = cli("library", "batch", "--from-file", path, "--stop-on-error", "--format", "json")
    finally:
        try:
            os.remove(path)
            os.rmdir(d)
        except OSError:
            pass
    results = []
    for line in out.splitlines():
        try:
            results.append(json.loads(line))
        except ValueError:
            continue
    return results


def default_name(sel):
    label = clean_text(sel.get("date_label", ""), 30)
    return ("On this day - %s" % label if label else "On this day")[:100]


def default_description(sel):
    return clean_text("Songs for %s: artists born or died on it, album anniversaries and number ones (on-this-day)." % (sel.get("date_label") or "a date"), 300)


def cmd_playlist(args):
    sel, uris = load_selection(args.selection, args.confirm, args.max_tracks, "save", "public" if args.public else "")
    name = (args.name or default_name(sel)).strip()[:100]
    if not name:
        die("The playlist needs a name.")
    if args.dry_run:
        print(json.dumps({"would_create": name, "private": not args.public, "tracks": len(uris)}, ensure_ascii=False))
        return
    try:
        res = run_batch([{"op": "playlist_create", "name": name}])
        new_uri = (res[0].get("result") or {}).get("uri") if res else None
        if not new_uri or not PLAYLIST_URI.fullmatch(str(new_uri)):
            die("The playlist could not be created: %s" % (res[:1],), 8)
        for i in range(0, len(uris), 100):
            r = run_batch([{"op": "playlist_add", "playlist_uri": new_uri, "track_uris": uris[i:i + 100]}])
            if not r or not r[0].get("ok", True):
                raise CliError("playlist_add failed: %s" % (r[:1],))
        run_batch([{"op": "playlist_update", "playlist_uri": new_uri, "description": default_description(sel), "public": bool(args.public)}])
        check = cli_json("playlist", "get", new_uri, "--no-tracks")
    except CliError as e:
        die("Spotify refused: %s" % e, 8)
    print(json.dumps({"created": name, "uri": new_uri, "private": not args.public, "tracks_added": len(uris),
                      "track_count_in_spotify": check.get("track_count")}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("charts")
    sp.set_defaults(fn=cmd_charts)

    sp = sub.add_parser("discover")
    sp.add_argument("--date", metavar="YYYY-MM-DD", help="the day to look up (only month and day matter); default today")
    sp.add_argument("--album-years", choices=["round", "all"], default="round", help="round = 5, 10, 15... years ago plus this and last year")
    sp.add_argument("--chart", action="append", metavar="ID", help="limit the number ones to these built-in chart ids; default all")
    sp.add_argument("--workers", type=int, default=4, help="Spotify lookups at the same time (1 to 8); Wikipedia is always read one page at a time")
    sp.add_argument("--show", type=int, default=10, help="how many matched items per angle, and how many that could not be matched, are printed; 0 = all")
    sp.add_argument("--max-number-ones", type=int, default=400, help="the most number ones looked up on Spotify in one run (1 to 2000)")
    sp.add_argument("--out", required=True, help="where to write the candidates (a private folder)")
    sp.set_defaults(fn=cmd_discover)

    sp = sub.add_parser("compose")
    sp.add_argument("--candidates", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--auto", action="store_true", help="about --count songs, up to --per-angle from each angle, shuffled")
    sp.add_argument("--count", type=int, default=20)
    sp.add_argument("--per-angle", type=int, default=5)
    sp.add_argument("--angles", type=lambda s: s.split(","), help="only these angles: born,died,album,number_one")
    sp.add_argument("--seed", type=int, default=None, help="makes the shuffle repeatable")
    sp.add_argument("--item", action="append", metavar="N[:K|:album]", help="an item from discover: its most played song, its K most played, or the whole album")
    sp.add_argument("--shuffle", action="store_true")
    sp.set_defaults(fn=cmd_compose)

    sp = sub.add_parser("queue")
    sp.add_argument("--selection", required=True)
    sp.add_argument("--confirm", required=True, metavar="CODE", help="the confirm_code of the list the user agreed to")
    sp.add_argument("--position", choices=["end", "front"], default="end")
    sp.add_argument("--max-tracks", type=int, default=75)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_queue)

    sp = sub.add_parser("playlist")
    sp.add_argument("--selection", required=True)
    sp.add_argument("--confirm", required=True, metavar="CODE")
    sp.add_argument("--name", help="name of the new playlist")
    sp.add_argument("--public", action="store_true", help="make the playlist public (default: private); needs the confirm_code_public of the list")
    sp.add_argument("--max-tracks", type=int, default=200)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_playlist)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

