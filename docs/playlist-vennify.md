# playlist-vennify

Compares two or more Spotify playlists like a Venn diagram: what they have in common, what is only in one of them, everything combined, or what appears in several. Then it adds the result to your queue or saves it as a new playlist, after you say yes.

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

The playlists have to be in your Spotify library (saved or followed). Spotify-made playlists such as Release Radar work too. If a name matches several playlists, the skill lists them and asks which one you mean.

## Comparisons

| Comparison | What it selects |
|------------|-----------------|
| Overlap | Tracks that are in **all** the playlists. |
| Unique | Tracks that are in **only one** playlist. Name one playlist ("only on *Road trip*") to get only its unique tracks. |
| Combined | Everything from all the playlists, each track once. |
| In at least N | With 3 or more playlists: tracks that appear in at least N of them (for example 2 of 4). |

You can compare 2 to 10 playlists at once. The report shows the size of every region, so you can see for example that 5 tracks are shared by A and C only, and 1 by all three.

## Options

| Option | Default | Notes |
|--------|---------|-------|
| Matching | the same Spotify track | Say "same song" to also match a single, album or remaster version by artist and title. `(feat. ...)` and `- Remastered` endings are ignored. It can merge two different songs with the same title by the same artist, so the skill shows which pairs it merged. |
| Order | as in the playlists | Say "shuffled", or "alternating" to alternate between the playlists. |
| Queue position | the end of the queue | Say "play next" or "at the front" to put the tracks in front of what you already queued. |
| Result | a report, then you choose | Queue, a new private playlist, both, or only the report. |

## Safety and limits

- **Comparing only reads.** Adding to the queue and creating a playlist change your Spotify, and only happen after you say yes. The script also needs the confirmation code of the exact list you were shown, so a different list can't be added than the one you approved.
- **Limits you will be asked about:** more than 50 tracks for the queue, more than 200 for a new playlist.
- **A new playlist is always private.** The skill never changes visibility.
- **It cannot take tracks out of your queue again.** Clear them in Spotify if you change your mind. It never plays, pauses or skips, and never deletes anything.
- Playlist names, descriptions and track titles are treated as untrusted data. The agent takes facts from them and ignores any instructions inside.
- Episodes and local files in a playlist are skipped and counted. A track that appears twice in one playlist counts once.
- Only playlists in your library can be read.
- Adding to the queue may need a Spotify Premium account (not verified).
- Tested on macOS only (see the [main README](../README.md)).
