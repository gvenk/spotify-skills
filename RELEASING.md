# Releasing

How to publish a new version of the skills in this repository. A release is a git tag plus a zip that contains only the skill folder.

## Steps

1. Commit and push all changes to `main`. Check that the README and `SKILL.md` describe what the skill does now.
2. Zip only the skill folder, so the folder itself is at the top of the zip:
   ```bash
   cd skills && zip -r ../text-to-playlist.zip text-to-playlist && cd ..
   ```
   Check the contents with `unzip -l text-to-playlist.zip`. It should list `text-to-playlist/SKILL.md`.
3. Tag and publish the release with the zip attached, for example with the GitHub CLI:
   ```bash
   gh release create v1.0.0 text-to-playlist.zip --title "v1.0.0" --notes "What changed"
   ```
   Or create the release on the GitHub releases page and upload the zip there.
4. The download link for the newest release is then
   `https://github.com/gvenk/spotify-skills/releases/latest/download/text-to-playlist.zip`.
5. Delete the local zip, or keep it out of git. It is a build file, not part of the repository.

## Versions

Use `vMAJOR.MINOR.PATCH`. Raise the major number when a change breaks how people call the skill, the minor number when you add an option or a mode, and the patch number for fixes and wording.

## Moving the repository

If the repository moves to another GitHub account or organisation, use GitHub's **Transfer ownership** (Settings, Danger Zone). GitHub then redirects the old repository, issue and download URLs. Afterwards update the URLs in the `README.md` (download links, issue links) and in this file.
