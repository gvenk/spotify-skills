# Releasing

How to publish a new version of the skills in this repository. A release is a git tag plus one zip per skill, each containing only that skill's folder.

## Steps

1. Commit and push all changes to `main`. Check that the README, the pages in `docs/` and each `SKILL.md` describe what the skills do now.
2. Zip only the skill folders, so each folder itself is at the top of its zip:
   ```bash
   cd skills
   zip -r ../text-to-playlist.zip text-to-playlist -x '*.DS_Store'
   zip -r ../radio-dj.zip radio-dj -x '*.DS_Store' '*__pycache__*'
   zip -r ../playlist-vennify.zip playlist-vennify -x '*.DS_Store' '*__pycache__*'
   zip -r ../playlist-watch.zip playlist-watch -x '*.DS_Store' '*__pycache__*'
   zip -r ../birthday-number-ones.zip birthday-number-ones -x '*.DS_Store' '*__pycache__*'
   cd ..
   ```
   Check the contents with `unzip -l radio-dj.zip` (and the same for the others). A zip with a script should list `<skill>/SKILL.md` and `<skill>/scripts/<script>.py`, and nothing else (`birthday-number-ones` also has `scripts/charts.json`).
3. Tag and publish the release with the zips attached, for example with the GitHub CLI:
   ```bash
   gh release create vX.Y.Z text-to-playlist.zip radio-dj.zip playlist-vennify.zip playlist-watch.zip birthday-number-ones.zip --title "vX.Y.Z" --notes "What changed"
   ```
   Or create the release on the GitHub releases page and upload the zips there.
4. The download links for the newest release are then
   `https://github.com/gvenk/spotify-skills/releases/latest/download/<skill>.zip`, for each of the five skills. The README links to them, so a new skill's link only works once a release with its zip exists: release before, or together with, the merge that adds the link.
5. Delete the local zips, or keep them out of git. They are build files, not part of the repository.

## Versions

Use `vMAJOR.MINOR.PATCH`. Raise the major number when a change breaks how people call the skill, the minor number when you add an option or a mode, and the patch number for fixes and wording.

## Moving the repository

If the repository moves to another GitHub account or organisation, use GitHub's **Transfer ownership** (Settings, Danger Zone). GitHub then redirects the old repository, issue and download URLs. Afterwards update the URLs in the `README.md` (download links, issue links) and in this file.
