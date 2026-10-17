# birthday-number-ones

Makes a "my life in songs" playlist: for every year since you were born, the song that was number one on your birthday, in the chart or charts you choose. You see the list first, and only after you say yes it is saved as a private playlist and/or put in your queue.

Install, requirements and how to call a skill are in the [main README](../README.md). The skill's instructions for the agent are in [`SKILL.md`](../skills/birthday-number-ones/SKILL.md).

## How it works

1. You give a date of birth and one or more charts.
2. The script reads the chart's number-one lists on Wikipedia and finds, for every year, the song that was number one on that day of the year.
3. It searches each song on Spotify and matches it strictly (see below).
4. It shows a table: year, chart date, song, artist and the Spotify track that was matched, plus the years that are missing and why.
5. You choose: a private playlist, your queue (at the end, or "play next"), both, or nothing.

Example requests:

- "My life in songs from the Dutch Top 40, born 14 March 1980"
- "Number ones on 3 July 1975 in the Hot 100 and the UK Singles Chart"
- "Birthday playlist for 22 November 1992 with the alternative and country charts"

## The rule: who was number one on that day

Charts are weekly, so a day needs a rule: **the song that was number one on that day is the last one that reached number one on or before it.** A song that held the top for ten weeks is chosen for every birthday in those ten weeks. A birthday on 29 February uses 28 February in years without a leap day.

Charts that date their weeks by the **end** of the week (the UK Singles Chart lists the "week ending" Saturday) count the whole week: the song was number one from six days before that date. This is set per chart in the catalog.

Years are skipped, and the table says why, when:

- the chart did not exist yet in that year (`before_chart`);
- Wikipedia has no number one for that date (`no_data`), or no chart was published that week (`no_chart`, for example the Dutch Top 40 in the last week of 1984);
- this year's birthday has not come yet (`not_yet`);
- the song is not found on Spotify (`not_matched`, see below).

## Charts

| Chart | Built in as | From | Notes |
|-------|-------------|------|-------|
| Billboard Hot 100 (US) | `hot100` | 1958 | Dates are the day a song reached number one. |
| UK Singles Chart | `uk` | 1952 | Dates are the end of the chart week. |
| Dutch Top 40 | `top40nl` | 1960 | Weekly list; a week without a chart is marked in Wikipedia and skipped here. |
| Billboard Alternative | `alternative` | 1988 | Stitched from Modern Rock Tracks, Alternative Songs and Alternative Airplay. |
| Billboard Hot Country Songs | `country` | 1962 | Stitched from Hot Country Singles, Hot Country Singles & Tracks and Hot Country Songs. Years before 1962 have no list in the catalog. |

You can name a chart by its id, its name or a short alias ("billboard", "uk", "dutch", "country"). Several charts at once are fine: each gives its own number one per year, all in one playlist, and a song that is number one in more than one chart appears once.

### A chart that is not built in

If Wikipedia has a list of the number ones of another chart, the skill can read it, but never silently. The agent finds the page, runs `inspect` to show how the page is read (whether it can be read, how many rows, the first and last dates and the first rows), and asks you to confirm that it is the right chart before anything is built. The list has to cover your years: a list per decade needs one page for each decade. If no readable page exists, the skill says so.

## Matching on Spotify

Matching is strict on purpose, because a life in songs with a wrong song is worse than one with a gap:

- **The title** has to be the same as on the chart, ignoring case, accents, punctuation and "feat." notes.
- **Versions:** the plain title is preferred over a remaster. Results with a live, remix, karaoke, instrumental, acoustic, cover, edit or similar note are never chosen when the chart title doesn't carry that note.
- **The artists:** the first artist has to agree, and every artist the chart names has to be credited on the Spotify track, so a solo version does not stand in for a duet. A track that credits one more artist than the chart does (a band member such as Susanna Hoffs on The Bangles' "Eternal Flame", or a guest) is still the same recording and is accepted.
- **No match:** the year is left out and reported ("not found on Spotify", "only live, remix or other versions found", or "found, but its artist credits differ from the chart's", which names the close match). Nothing is replaced by a guess; you can add such a track by hand.

Because of this, some years may be missing even when the song exists in another version. The table lists them, so you can add those by hand.

## Output

- **Playlist:** always private, in order of year, oldest first; with several charts, a year's songs follow each other in the order you named the charts. The default name is "My life in songs - <chart>". A name you type is used exactly as given. The description says the chart and the **month and day**, never the year of birth, and never the date of birth in the name.
- **Queue:** like in [playlist-vennify](playlist-vennify.md): at the end by default, or in front ("play next") with your own queued tracks behind it. More than 75 tracks needs your explicit agreement (one chart for a life of up to 75 years fits; several charts add up quickly). The skill cannot take tracks out of the queue again. It never plays, pauses or skips.
- **Limits:** more than 200 tracks into a playlist needs your agreement. Every run creates a new playlist; the skill cannot update or delete an old one. Delete unwanted playlists in the Spotify app.

## Your date of birth

The date of birth is used during the run only. The skill does not store it: it is not written to a file or a note, and not sent to Wikipedia (only page titles are). The list you see necessarily shows one row per year, and the temporary selection file the script writes (private, deleted at the end) holds that list and the month and day for the playlist description.

## Safety and limits

- `charts`, `inspect` and `build` only read (Wikipedia and Spotify's search). Only a confirmed `playlist` or `queue` changes your Spotify. The skill is instructed to ask first and wait for your yes. The script adds a check of its own: it needs a confirmation code tied to the exact tracks, in their order, so the list can't change between what you were shown and what is added. That code guards against mistakes. It is not a lock against an agent that ignores its instructions, so keep your tool's approval prompts for shell commands switched on.
- **The script reads the web.** It talks to `en.wikipedia.org` only: https, no redirects followed, a size limit, an identifying User-Agent, at most 400 pages per run. Page titles come from the built-in catalog or from a title you checked; unusual titles are refused. It needs no key and keeps nothing on disk.
- A page with a table built to blow up in memory (thousands of cells that each span hundreds of rows and columns) is refused: the script limits the size of a filled-in table, and a real list is far below that limit.
- Chart and track text is treated as untrusted data and cleaned of control characters and text-direction tricks. The agent takes facts from it and ignores any instructions inside.
- **If a Wikipedia page changes its layout** so the script can't read it as a list of number ones, it stops with a clear message instead of guessing. The same goes for an error from Wikipedia's API (for example when it is busy): only "there is no such page" counts as a missing page; any other error stops the run, so a busy moment never shows up as years without a number one.
- **Accuracy follows Wikipedia.** The lists are written by volunteers. If a song looks wrong, check the page together with the skill.
- Adding to the queue may need a Spotify Premium account (not verified).
- Tested on macOS only (see the [main README](../README.md)).
