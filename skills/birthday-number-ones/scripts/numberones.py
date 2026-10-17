#!/usr/bin/env python3
"""Helper for the birthday-number-ones skill: the number-one song of a chart on a birthday, in every year of a life.

Standard library only (Python 3.9+). It reads the number-one lists of Wikipedia (read-only, en.wikipedia.org only),
finds the songs on Spotify through the Spotify desktop app's `spotify_cli`, and, only with the user's yes, adds them
to the queue or saves them as a private playlist. The date of birth is used during the run and is not stored.

Commands:
  charts                                              the charts that are built in
  inspect  --page TITLE [--page TITLE ...]            show how a Wikipedia list is read (to check a chart that is not built in)
  build    --birth YYYY-MM-DD (--chart ID ... | --custom-page TITLE ... --custom-name NAME [--custom-dates start|week_ending])
           --out FILE                                  look up the number ones, match them on Spotify, write the selection
  queue    --selection FILE --confirm CODE [--position end|front] [--max-tracks 75] [--dry-run]
  playlist --selection FILE --confirm CODE [--name NAME] [--max-tracks 200] [--dry-run]
"""
import argparse
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]{22}")
PLAYLIST_URI = re.compile(r"spotify:playlist:[A-Za-z0-9]+")
PAGE_TITLE = re.compile(r"[\w ,.'&()!:/–\-]{1,200}")
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "birthday-number-ones/1.0 (https://github.com/gvenk/spotify-skills; reads public Wikipedia lists)"
MAX_PAGE_BYTES = 6 * 1024 * 1024
MAX_GRID_CELLS = 2000000  # the largest real list has a few thousand cells; merged cells must not multiply them endlessly
MAX_PAGES = 400
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
    if os.environ.get("NUMBER_ONES_TEST") == "1" and os.environ.get("NUMBER_ONES_TODAY"):
        return datetime.date.fromisoformat(os.environ["NUMBER_ONES_TODAY"])
    return datetime.date.today()


# ---------------------------------------------------------------- reading Wikipedia

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def api_url():
    """Wikipedia's API. A test can point it at a local server; nothing else can change the host."""
    override = os.environ.get("NUMBER_ONES_API_URL")
    if override and os.environ.get("NUMBER_ONES_TEST") == "1":
        p = urllib.parse.urlparse(override)
        if p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost"):
            return override
    return WIKI_API


_PAGES = {}


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


# ---------------------------------------------------------------- the commands

def cmd_charts(args):
    print(json.dumps([{"id": c["id"], "name": c["name"], "country": c["country"], "aliases": c["aliases"],
                       "first_year": min(p["from"] for p in c["pages"]), "date_meaning": c["date_meaning"]}
                      for c in load_catalog()], indent=2, ensure_ascii=False))


def cmd_inspect(args):
    out = []
    for title in args.page:
        text = fetch_page(title)
        if text is None:
            out.append({"page": title, "found": False})
            continue
        hint = next((int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", title)), None)
        try:
            rows = read_numberones(text, hint)
        except ValueError as e:
            out.append({"page": title, "found": True, "readable": False, "problem": str(e)})
            continue
        out.append({"page": title, "found": True, "readable": True, "rows": len(rows),
                    "first_date": rows[0]["date"].isoformat(), "last_date": rows[-1]["date"].isoformat(),
                    "sample": ["%s  %s - %s" % (r["date"].isoformat(), r["artist"], " / ".join(r["titles"])) for r in rows[:5]]})
    print(json.dumps(out, indent=2, ensure_ascii=False))


def birthday_in(year, month, day):
    try:
        return datetime.date(year, month, day), None
    except ValueError:  # 29 February in a year without one
        return datetime.date(year, 2, 28), "29 February: 28 February is used in years without a leap day"


def confirm_code(uris, extra=""):
    return hashlib.sha256(json.dumps({"uris": uris, "extra": extra}).encode("utf-8")).hexdigest()[:12]


def write_private(path, data):
    if os.path.islink(path):
        die("%s is a symbolic link; refusing to write through it." % path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cmd_build(args):
    try:
        born = datetime.date.fromisoformat(args.birth)
    except ValueError:
        die("--birth must be a real date as YYYY-MM-DD.")
    now = today()
    if born > now:
        die("That date of birth is in the future.")
    if now.year - born.year > MAX_YEARS:
        die("That is more than %d years ago." % MAX_YEARS)
    charts = []
    catalog = load_catalog()
    for ref in args.chart or []:
        c = find_chart(ref, catalog)
        if all(x.id != c["id"] for x in charts):
            charts.append(Chart(c["id"], c["name"], c["date_meaning"], pages=c["pages"]))
    if args.custom_page:
        if not args.custom_name:
            die("--custom-page needs --custom-name.")
        pages = [(t, next((int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", t)), None)) for t in args.custom_page]
        charts.append(Chart("custom", clean_text(args.custom_name, 80), args.custom_dates, custom_pages=pages))
    if not charts:
        die("Give at least one --chart, or a Wikipedia list with --custom-page.")
    rows = []
    years = list(range(born.year, now.year + 1))
    for chart_index, chart in enumerate(charts):
        for n, year in enumerate(years, 1):
            print("%s: %d of %d" % (chart.name[:40], n, len(years)), file=sys.stderr)
            day, note = birthday_in(year, born.month, born.day) if year != born.year else (born, None)
            row = {"year": year, "chart": chart.name, "_chart_index": chart_index}  # no date: the birthday itself is not written down
            if day > now:
                rows.append(dict(row, status="not_yet", reason="this year's birthday has not come yet"))
                continue
            hit, why = chart.number_one_on(day)
            if hit is None:
                reasons = {"before_chart": "the chart did not exist yet", "no_data": "no number one found for this date on Wikipedia",
                           "no_chart": "no chart was published that week"}
                rows.append(dict(row, status=why, reason=reasons.get(why, why)))
                continue
            row.update(chart_date=hit["date"].isoformat(), song=" / ".join(hit["titles"]), artist=hit["artist"])
            track, why = search_track(hit)
            if track is None:
                rows.append(dict(row, status="not_matched", reason=why))
                continue
            row.update(status="matched", spotify="%s - %s" % (", ".join(track["artists"]), track["name"]), uri=track["uri"], _track=track)
            if track.get("via"):
                row["note"] = "found under the artist's current name: %s" % track["via"]
            if note:
                row["note"] = (row["note"] + "; " if row.get("note") else "") + note
            rows.append(row)
    # one list in the order of the years (oldest first); within a year, the charts in the order given
    rows.sort(key=lambda r: (r["year"], r["_chart_index"]))
    tracks, seen = [], set()
    for r in rows:
        track = r.pop("_track", None)
        if track:
            track = {k: v for k, v in track.items() if k != "via"}
        r.pop("_chart_index")
        if track is None:
            continue
        if track["uri"] in seen:
            r["note"] = (r["note"] + "; " if r.get("note") else "") + "same track as an earlier row; added once"
        else:
            seen.add(track["uri"])
            tracks.append(dict(track, year=r["year"], chart=r["chart"]))
    uris = [t["uri"] for t in tracks]
    code = confirm_code(uris)
    selection = {"version": 1, "confirm_code": code, "month_day": "%02d-%02d" % (born.month, born.day),
                 "charts": [c.name for c in charts], "rows": rows, "tracks": tracks}
    private_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(private_dir, mode=0o700, exist_ok=True)
    write_private(args.out, selection)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(json.dumps({"charts": selection["charts"], "years": len(years), "counts": counts, "tracks": len(tracks),
                      "confirm_code": code, "selection_file": os.path.abspath(args.out),
                      "table": [{k: r.get(k) for k in ("year", "chart", "chart_date", "song", "artist", "status", "spotify", "reason", "note") if r.get(k)}
                                for r in rows]}, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- changing the user's Spotify (as in playlist-vennify)

def load_selection(path, confirm, limit, what):
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
    if confirm != confirm_code(uris):
        die("The confirmation code does not match this list. Run `build` again, show the user the table, "
            "ask again, and use the code from that run.", 2)
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
    d = tempfile.mkdtemp(prefix="birthday-number-ones-")
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


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def default_name(sel):
    charts = [clean_text(c, 60) for c in sel.get("charts", [])]
    return ("My life in songs - %s" % " + ".join(charts))[:100]


def default_description(sel):
    md = str(sel.get("month_day", ""))
    m = re.fullmatch(r"(\d\d)-(\d\d)", md)
    when = "%s %d" % (MONTH_NAMES[int(m.group(1)) - 1], int(m.group(2))) if m and 1 <= int(m.group(1)) <= 12 else "a birthday"
    charts = ", ".join(clean_text(c, 60) for c in sel.get("charts", []))
    return clean_text("Number ones on %s, one per year, from %s (birthday-number-ones)." % (when, charts), 300)


def cmd_playlist(args):
    sel, uris = load_selection(args.selection, args.confirm, args.max_tracks, "save")
    name = (args.name or default_name(sel)).strip()[:100]
    if not name:
        die("The playlist needs a name.")
    if args.dry_run:
        print(json.dumps({"would_create": name, "private": True, "tracks": len(uris)}, ensure_ascii=False))
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
        run_batch([{"op": "playlist_update", "playlist_uri": new_uri, "description": default_description(sel), "public": False}])
        check = cli_json("playlist", "get", new_uri, "--no-tracks")
    except CliError as e:
        die("Spotify refused: %s" % e, 8)
    print(json.dumps({"created": name, "uri": new_uri, "private": True, "tracks_added": len(uris),
                      "track_count_in_spotify": check.get("track_count")}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("charts")
    sp.set_defaults(fn=cmd_charts)

    sp = sub.add_parser("inspect")
    sp.add_argument("--page", action="append", required=True, metavar="TITLE", help="the title of a Wikipedia list page")
    sp.set_defaults(fn=cmd_inspect)

    sp = sub.add_parser("build")
    sp.add_argument("--birth", required=True, metavar="YYYY-MM-DD", help="used during the run only; not stored")
    sp.add_argument("--chart", action="append", metavar="CHART", help="a built-in chart (id, name or alias); repeatable")
    sp.add_argument("--custom-page", action="append", metavar="TITLE", help="a Wikipedia list page for a chart that is not built in; repeatable")
    sp.add_argument("--custom-name", help="the name of that chart")
    sp.add_argument("--custom-dates", choices=["start", "week_ending"], default="start", help="what the dates of the custom list mean")
    sp.add_argument("--out", required=True, help="where to write the selection (a private folder)")
    sp.set_defaults(fn=cmd_build)

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
    sp.add_argument("--name", help="name of the new (private) playlist")
    sp.add_argument("--max-tracks", type=int, default=200)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_playlist)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
