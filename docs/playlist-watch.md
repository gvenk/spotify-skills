# playlist-watch

Shows what changed in your Spotify playlists since you last looked: the tracks that were added and removed. You can watch all your playlists or only a few, and then put the new tracks in your queue or in a playlist, after you say yes.

Install, requirements and how to call a skill are in the [main README](../README.md). The skill's instructions for the agent are in [`SKILL.md`](../skills/playlist-watch/SKILL.md).

## How it works

1. **Watch.** Tell it which playlists to watch: all of your library, or the ones you name. For each one it takes a *baseline*: a snapshot of the track list as it is now, stored on your computer.
2. **Check.** Ask "what changed?". It reads the watched playlists again and compares them with the snapshots. The report shows, per playlist, the tracks added and removed. Checking only reads: ask twice and you get the same answer.
3. **Mark as seen.** When you confirm, the snapshots move forward to what you were shown. Until then the changes stay on the list.
4. **Merge (optional).** Take the new tracks to your queue, a new private playlist, or an existing playlist of yours.

Example requests:

- "Watch all my playlists"
- "Watch *Road trip* and `https://open.spotify.com/playlist/...`"
- "What changed in my playlists?"
- "Mark these changes as seen"
- "Add the new tracks of *3voor12 #NIEUWEMUZIEK* to my queue"
- "Put the new tracks of my watched playlists in a new playlist called *New finds*"
- "Stop watching *Road trip*" / "forget everything"

## What is watched

| Setting | What it does |
|---------|--------------|
| All my library | Every playlist in your library, **including those inside folders** (nested folders too), re-read at each check. A playlist you save later is reported once as *new* and baselined when you confirm. One that leaves your library is reported as *left your library*; its snapshot is kept until you `forget` it. |
| Selected playlists | Playlists you name, by name (from your library), `spotify:playlist:` link or `open.spotify.com` link. They stay watched even if you unfollow them, as long as they can be read. |
| Outside your library | **Public** playlists work, given by their link. A private playlist of someone else cannot be read by the Spotify app's command line, so it cannot be watched. There is no way to list playlists you don't have: you give the links. |

You can combine the two. `forget` takes a playlist off the list; in "all my library" mode it is also excluded, so it doesn't come back. Naming it again with *watch* brings it back.

## What counts as a change

- **Added:** a track that is in the playlist now and wasn't in the snapshot.
- **Removed:** a track that was in the snapshot and is gone. It is shown in the report, but the merge never uses it.
- **Reordered:** the same tracks in a new order. Shown as one line, not as new tracks.
- **Renamed, or a new description:** shown as a small note.
- **Matching:** by Spotify track. If a song is swapped for its remaster, that is one removed and one added.
- **Unreadable:** a playlist that can't be read right now (made private, deleted, connection) is reported as such and keeps its old snapshot. It is never treated as "all tracks removed".
- Episodes and local files are skipped and counted. A track that appears twice in one playlist counts once.

## Limits of "changed"

- **No dates.** The Spotify app's command line doesn't tell when a track was added, or by whom. Changes are "new since you last confirmed". A track added and removed again between two confirmations is not seen.
- **No background watching.** The skill runs when you ask. Scheduling it (a daily check, for example) is possible later with your system's scheduler; the `check` command only reads, so it is safe to schedule.
- **A full read each time.** Every watched playlist is read completely, which takes about 25 seconds for 90 playlists and about 40 for 140. Counting tracks would be faster but would miss a swapped song, so it is not used.

## Merging the new tracks

A merge takes the **added** tracks of the playlists you pick (all that changed, by default), each track once, even if several playlists added it.

| Destination | What happens |
|-------------|--------------|
| Queue | The tracks are added to the end of your queue. The skill can't take them out again. More than 50 needs your agreement. |
| New playlist | A new **private** playlist with the tracks. |
| Existing playlist, *append* | The tracks are added at the end of a playlist you own. Tracks already in it are skipped, so repeating a merge doesn't pile up duplicates. |
| Existing playlist, *overwrite* | The content is replaced with the new tracks. The skill first shows how many tracks will be removed from which playlist, and asks for a clear yes naming it. The old content is saved first in the data folder (`backups/`) as a JSON file, because the Spotify app's command line can't undo it. |

Rules:

- The target must be **your** playlist, and it must not be one of the source playlists or one on your explicit watch list. A playlist the skill merges into is not watched afterwards, so the skill's own edits don't show up as someone else's changes.
- Every merge starts as a plan: you see the sources, the number of tracks, the tracks, what is skipped and what would be removed. Nothing changes until you say yes.
- After a successful merge the snapshots of the source playlists move forward. If the merge fails, they stay where they were.
- More than 200 tracks into a playlist needs your agreement; overwriting a playlist of more than 2000 tracks isn't supported.

## Your data and how to remove it

The skill keeps its data in a folder of its own, so reinstalling or updating the skill doesn't touch it:

| System | Folder |
|--------|--------|
| macOS, Linux | `~/.local/share/playlist-watch/` (or `$XDG_DATA_HOME/playlist-watch/`) |
| Windows (untested) | `%LOCALAPPDATA%\playlist-watch\` |
| Any | the folder named in `PLAYLIST_WATCH_DIR`, if set |

It contains:

| File | What it is |
|------|------------|
| `README.txt` | A short notice about what the folder is and how to remove it. |
| `watch.json` | What you watch, and the playlists you merged into. |
| `snapshots/` | One file per watched playlist: its track list at your last confirmation. |
| `backups/` | The old content of a playlist, saved just before an overwrite. |

It holds no passwords, keys or Spotify login, and nothing is sent anywhere. The folder and its files can be read only by you.

**When you remove the skill, remove its data too.** Ask the skill to *forget everything*: `forget --all` says what it would delete, and after your yes it deletes the watch list and snapshots. The overwrite backups are kept unless you also say so (they are your only way back after an overwrite). Or delete the folder yourself, for example `rm -r ~/.local/share/playlist-watch`.

## Safety and limits

- **Looking only reads.** `watch`, `check` and `status` never change your Spotify; `seen` and `forget` change only local files. Only a merge changes Spotify, and the skill is instructed to ask first and wait for your yes. The script adds a check of its own: it needs a confirmation code tied to the exact tracks, destination and mode (and, for an overwrite, the target playlist), so the plan can't change between what you were shown and what happens. That code guards against mistakes. It is not a lock against an agent that ignores its instructions, so keep your tool's approval prompts for shell commands switched on.
- **The only destructive action is an overwrite,** and only on a playlist you own and named, after a backup. The skill never deletes a playlist, never makes one public, and never plays, pauses or skips.
- Playlist names, descriptions and track titles are untrusted data. The agent takes facts from them and ignores any instructions inside. The script also removes control characters and text-direction tricks, tidies whitespace and caps the length before anything is printed or stored.
- A damaged data file stops the skill with a clear message and the file name; it never silently starts over. Data files are written atomically, and the folder is never followed through a symbolic link.
- Adding to the queue may need a Spotify Premium account (not verified).
- Tested on macOS only (see the [main README](../README.md)).
