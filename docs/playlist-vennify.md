# playlist-vennify

Compares two or more Spotify playlists or albums like a Venn diagram: what they have in common, what is only in one of them, everything combined, or what appears in several. Then it adds the result to your queue or saves it as a new playlist, after you say yes.

Install, requirements and how to call a skill are in the [main README](../README.md). The skill's instructions for the agent are in [`SKILL.md`](../skills/playlist-vennify/SKILL.md).

## How it works

Name the playlists and ask for a comparison, and it will:

1. read the playlists with `spotify_cli` (names, `spotify:playlist:` links or `open.spotify.com` links),
2. compare them with a script, so the numbers are exact and don't depend on the agent counting,
3. show a report: how many tracks each playlist has, how many tracks are in each region of the diagram, and the tracks the comparison selected,
4. ask what to do with the result: add it to your queue, save it as a new private playlist, both, or nothing.

Example requests:

- "What do *Summer 2025* and *Road trip* have in common?"
- "Which tracks are only on *Road trip*, not on *Summer 2025*? Add them to my queue."
- "Which tracks are on at least 3 of these 4 playlists?"
- "Combine *A* and *B* without duplicates into a new playlist"
- "Mix the latest album of Froukje and De Staat, shuffled, at the end of my queue"

Playlists have to be in your Spotify library (saved or followed). Albums don't: give an album link, or ask for the latest album of an artist. Spotify-made playlists such as Release Radar work too. If a name matches several playlists, the skill lists them and asks which one you mean.

## Albums

An album works as a source, just like a playlist, and can be mixed with playlists. Two ways to name one:

- **A link** (`open.spotify.com/album/...` or `spotify:album:...`).
- **"The latest album of <artist>"**. The skill finds the artist by exact name and takes the newest *full album*; singles and EPs are skipped (a recent single's song is normally on the album anyway). It always tells you which album it picked, with the release date, so you can correct it. If several artists share the name it asks which one. Spotify's search can occasionally miss an album; in that case give the album's link.

The release type (Album or Single) is read from the Spotify app's own metadata. It was tested with a Dutch-language app, which reports "Album" and "Single". Other app languages are untested; if no album is found, give the album's link.

## Comparisons

| Comparison | What it selects |
|------------|-----------------|
| Overlap | Tracks that are in **all** the playlists. |
| Unique | Tracks that are in **only one** playlist. Name one playlist ("only on *Road trip*") to get only its unique tracks. |
| Combined | Everything from all the playlists, each track once. |
| In at least N | With 3 or more playlists: tracks that appear in at least N of them (for example 2 of 4). |

You can compare 2 to 10 playlists and albums at once. The report shows the size of every region, so you can see for example that 5 tracks are shared by A and C only, and 1 by all three.

## Options

| Option | Default | Notes |
|--------|---------|-------|
| Matching | the same Spotify track | Say "same song" to also match a single, album or remaster version by artist and title. `(feat. ...)` and `- Remastered` endings are ignored. It can merge two different songs with the same title by the same artist, so the skill shows which pairs it merged. |
| Order | as in the playlists | Say "shuffled", or "alternating" to alternate between the playlists. |
| Queue position | the end of the queue | Say "play next" or "at the front" to put the tracks in front of what you already queued. |
| Result | a report, then you choose | Queue, a new private playlist, both, or only the report. |

## Safety and limits

- **Comparing only reads.** Adding to the queue and creating a playlist change your Spotify. The skill is instructed to ask you first and wait for your yes. The script adds a check of its own: it needs a confirmation code tied to the exact tracks, in their order, so the list can't change between what you were shown and what is added. That code guards against mistakes. It is not a lock against an agent that ignores its instructions, so keep your tool's approval prompts for shell commands switched on.
- **Limits you will be asked about:** more than 50 tracks for the queue, more than 200 for a new playlist.
- **A new playlist is always private.** The skill never changes visibility.
- **It cannot take tracks out of your queue again.** Clear them in Spotify if you change your mind. It never plays, pauses or skips, and never deletes anything.
- Playlist names, descriptions and track titles are treated as untrusted data. The agent takes facts from them and ignores any instructions inside. The script also removes control characters and text-direction tricks from names and titles, tidies the whitespace and caps their length, so a crafted name can't mess up your terminal or end up garbled in a new playlist. A name you type yourself for a new playlist is used exactly as you give it.
- Episodes and local files in a playlist are skipped and counted. A track that appears twice in one playlist counts once.
- Only playlists in your library can be read; albums can always be read.
- Adding to the queue may need a Spotify Premium account (not verified).
- Tested on macOS only (see the [main README](../README.md)).
