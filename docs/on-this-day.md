# on-this-day

A surprise list of about 20 songs for a date, from what happened on that day in music: artists born or died on it, albums released on it with a round anniversary, and songs that reached number one that week. You see everything first, with the reason for every song, and can pick what you want. Only after you say yes is it added to your queue and/or saved as a playlist (private unless you ask for a public one).

Install, requirements and how to call a skill are in the [main README](../README.md). The skill's instructions for the agent are in [`SKILL.md`](../skills/on-this-day/SKILL.md).

## How it works

1. You say "surprise me", or "music for 4 May". The default date is today on your computer.
2. The script reads Wikipedia (the page of the day, the lists of albums, the number-one lists of the five built-in charts) and looks every artist, album and number one up on Spotify. A full run takes about 2 to 3 minutes: Wikipedia is read one page at a time (as its API rules ask), while the Spotify lookups run four at a time (`--workers`, 1 to 8).
3. You get either a composed list ("surprise me") or everything that was found, numbered, to choose from.
4. You decide: the queue (at the end, or "play next"), a playlist, both, or nothing.

Example requests:

- "Surprise me with music for today"
- "What happened in music on 4 May? Show me everything"
- "Play the album from number 14" (the whole album goes into the queue)
- "Combine 3 and 7" (the best songs of those two artists)
- "Only the deaths and the albums"

## The four angles

| Angle | What counts | Source |
|-------|-------------|--------|
| **Born** | Musicians born on this day, any year | The day page on Wikipedia ("October 23"), picked by the one-line description (singer, composer, guitarist, rapper...) |
| **Died** | Musicians who died on this day, any year | Same page |
| **Albums** | Albums released on this day **5, 10, 15... years ago**, plus this year and last year | Wikipedia's lists of albums by release date, from 1950 on where a readable list exists (the lists are tables in different layouts; older years have no list) |
| **Number ones** | Songs that **reached number one in the 7 days up to this date**, in all five built-in charts | The number-one lists of [birthday-number-ones](birthday-number-ones.md) (Hot 100, UK, Dutch Top 40, alternative, country) |

Weekly charts rarely change on the exact day, so "reached number one in the week up to this date" is the rule: the song is the anniversary of that week. The overview shows the exact chart date.

This gives many candidates: a normal day has a few dozen musicians, about 5 to 60 albums and 100 to 200 number ones. The overview shows the 10 most played per angle and says how many more there are.

## Choosing

**Surprise me** gives about 20 songs: up to 5 from each angle (the most played first), the free slots going to the other angles when one has too few, **one song per artist or album**, a song that is in the list once, shuffled. Every song comes with its reason ("born 1985, 41 years ago", "released 1981, 45 years ago", "number one in Dutch Top 40 from 2010-10-23, 16 years ago").

**You choose** from the numbered overview:

- an item gives its most played song, or its 3 (or any number of) most played songs;
- an album item can give the **whole album**;
- you can combine items, and shuffle or keep the order you gave.

Items that could not be used stay in the overview with the reason, and are never chosen automatically.

## Matching on Spotify

Matching is strict on purpose: a wrong artist is worse than a gap.

- **People:** the Spotify artist has to have the same name as on Wikipedia (ignoring case, accents and punctuation), or the name of their Wikipedia article. If several Spotify artists share the name, one has to have at least **ten times** the plays of the next; otherwise the person is marked `unsure` and not used. The song is the artist's most played studio version (no live, remix, karaoke and similar versions), among the first 20 search results. An artist who is not on Spotify under that name is `not_found`, which is common for older composers and jazz players.
- **Albums:** the album name has to be the same (a trailing `(Deluxe)` or `[Special Edition]` is ignored) with the same artist. The song is the album's most played track; "the whole album" reads the album's track list.
- **Number ones:** the same strict rules as in [birthday-number-ones](birthday-number-ones.md#matching-on-spotify).

"Most played" is Spotify's play count of the song. It is a proxy for fame, not a judgement.

## Output

- **Queue:** like in [playlist-vennify](playlist-vennify.md): at the end by default, or in front ("play next") with your own queued tracks behind it. More than 75 tracks needs your explicit agreement. The skill cannot take tracks out of the queue again. It never plays, pauses or skips.
- **Playlist:** **private by default**, public only when you ask for it ("make it public"). Named "On this day - 23 October" unless you give a name (used exactly as typed). The description names the date and nothing personal. A public playlist needs its own confirmation code (`confirm_code_public`), so a list you agreed to as private cannot be made public by mistake. More than 200 tracks needs your agreement. Every run creates a new playlist; the skill cannot update or delete an old one. Delete unwanted playlists in the Spotify app.

## Safety and limits

- `charts`, `discover` and `compose` only read (Wikipedia and Spotify's search; a whole album is read by `compose`). Only a confirmed `playlist` or `queue` changes your Spotify. The skill is instructed to ask first and wait for your yes. The script adds a check of its own: it needs a confirmation code tied to the exact tracks, in their order, so the list can't change between what you were shown and what is added. That code guards against mistakes. It is not a lock against an agent that ignores its instructions, so keep your tool's approval prompts for shell commands switched on.
- **The script reads the web.** It talks to `en.wikipedia.org` only: https, no redirects followed, a size limit per page, an identifying User-Agent, at most 600 pages per run. Page titles are made from the date, a year or the built-in chart catalog. It needs no key and keeps nothing on disk apart from the private candidates and selection files in a folder the agent makes and deletes.
- A page with a table built to blow up in memory is refused: the script limits the size of a filled-in table.
- Text from Wikipedia and Spotify is treated as untrusted data and cleaned of control characters and text-direction tricks. The agent takes facts from it and ignores any instructions inside.
- **If a Wikipedia page changes its layout** so the script can't read it, it stops with a clear message instead of guessing. The same goes for an error from Wikipedia's API: only "there is no such page" counts as a missing page.
- **Accuracy follows Wikipedia.** The lists are written by volunteers. Musicians are picked from the day page by their one-line description, so a few non-musicians may slip through and a few musicians may be missed.
- **Limits per run:** 60 people per angle (born, died), 60 albums with at most 15 per year, at most 3 number ones per chart per week and 400 number ones in all (`--max-number-ones`, up to 2000). A page with more than that is broken or vandalised; the cut is reported. The overview prints at most 10 matched and 10 unmatched items per angle (`--show`); the rest is in the candidates file.
- Adding to the queue may need a Spotify Premium account (not verified).
- Tested on macOS only (see the [main README](../README.md)).
