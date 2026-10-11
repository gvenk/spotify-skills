# text-to-playlist

Turns any text that mentions artists (a newsletter, agenda, festival line-up, article, review; a URL or pasted text) into a Spotify playlist, private or public as you choose.

Install, requirements and how to call a skill are in the [main README](../README.md). The skill's instructions for the agent are in [`SKILL.md`](../skills/text-to-playlist/SKILL.md).

## How it works

Give the agent a URL or paste the text, and it will:

1. read the text and extract the artists the text is about (artists mentioned only as history or comparison, such as acts from past editions, are left out),
2. find each artist on Spotify and pick tracks from both recent and older releases: the most played ones by default, or lesser-known "deep cuts" if you ask (reissues and remasters don't count as "recent"),
3. create a playlist (it asks whether it should be private or public, unless you said so), optionally inside a folder, and verify the result.

Example requests:

- "Make a playlist from this newsletter: `https://example.com/agenda`"
- "Agenda to playlist, 3 tracks per artist, call it *Summer concerts*"
- "Playlist from this agenda, put it in my *Newsletters* folder"
- "Make a playlist with exactly the songs in this list of winners" (see *Named songs* below)
- "Playlist from this line-up, shuffled"
- "Make a public playlist with the hidden gems of the artists in this review"
- "Playlist from this album chart, 2 deep cuts per album" (see *Album lists* below)
- "Playlist from this top 100, 1 track per album, in the order of the chart"

If you call the skill without a URL or text, or ask for help, it shows a short help message with examples and the options, and asks for input.

## Options

You can ask for these in plain language:

| Option | Default | Notes |
|--------|---------|-------|
| Tracks per artist | 2 recent + 2 older | Each group can be 0 to 5. "3 per artist" means 2 recent + 1 older. |
| Tracks per album | 2 | For texts that list albums (see *Album lists*). 0 to 5. |
| Playlist name | generated as `<Source> - <Theme> - <Month Year>` | A name you give is used exactly as written. |
| Selection style | hits (most played) | Say "deep cuts", "hidden gems" or "not just the hits" for less common songs, or "mix" for one hit and one deep cut per group. Doesn't apply to named songs. |
| Track order | in the order of the text | Say "shuffled" or "random order" to mix the tracks. This sets the order of the tracks in the playlist, not playback shuffle in Spotify. |
| Visibility | asked if not mentioned (private if it can't ask) | Say "public" or "private" in your request to skip the question. |
| Folder | none (top level) | An existing folder is used; a new one is created if the name does not exist. |
| Named songs | asked if the text names songs | If the text pairs artists with specific songs (winners, setlist, tracklist, a single chart), you can get exactly those songs instead of each artist's top tracks. |
| Album lists | asked if the text lists albums | For an album chart or best-albums list, you can get tracks from each listed album (the most played, or deep cuts) instead of the artist's top tracks. |

**Same-name artists.** Spotify can have several artists with the same name. The skill picks the one whose releases match the text. If the text gives no hint, it takes an artist only when they have at least 10 times the monthly listeners of every other match, and says in its report that this was a guess. Otherwise it skips the artist and lists them in the report.

**Large texts.** More than 40 artists makes the skill ask before it continues. If the settings would give more than 200 tracks, it tells you up front and uses fewer (for example 1 track per album instead of 2) to stay under 200. You can overrule that by saying how many you want.

## Safety and limits

- Playlists are **private** unless you choose public. If your request doesn't say, the skill asks, and falls back to private when it can't ask.
- Newsletter text is treated as untrusted data. The agent extracts artist names only and ignores any instructions inside the content.
- Artist and playlist names are never pasted unescaped into shell commands, and track URIs are validated before they are added.
- It only creates playlists and folders, and adds tracks. It does not play music, delete playlists, or remove folders.
- `spotify_cli` cannot delete playlists. Delete test or unwanted playlists in the Spotify app.
- Artists are matched by name, then by the artist behind the release. Artists that share a name on Spotify can still be mixed up, so check the result. Artists that cannot be found are listed in the final report.
- Tested on macOS only (see the [main README](../README.md)).
