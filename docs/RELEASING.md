# Cutting a release

Pre-built binaries are published to the repo's
[Releases](../../releases) page automatically by
[`.github/workflows/release.yml`](../.github/workflows/release.yml).

## To publish a new release

```sh
git tag v0.1.0           # use the version you're releasing
git push origin v0.1.0
```

Pushing a `v*` tag triggers the workflow, which:

1. Builds a single-file `secdogie-agent` executable on Linux, Windows, and
   macOS (via PyInstaller).
2. Builds the `secdogie-tunnel` binary on Linux and runs its unit tests.
   (The tunnel is Linux-only — it uses the Linux TUN device / ioctl API —
   so there is no Windows or macOS tunnel build.)
3. Zips each binary together with its README/LICENSE and attaches all the
   zips to a new GitHub Release named after the tag, with auto-generated
   notes.

## Resulting download assets

| Asset | Contents |
|-------|----------|
| `secdogie-agent-linux-x86_64-<tag>.zip`   | `secdogie-agent` + docs |
| `secdogie-agent-windows-x86_64-<tag>.zip` | `secdogie-agent.exe` + docs |
| `secdogie-agent-macos-arm64-<tag>.zip`    | `secdogie-agent` + docs |
| `secdogie-tunnel-linux-x86_64-<tag>.zip`  | `secdogie-tunnel` + protocol docs |

## Testing the build without publishing

Trigger the workflow manually from the **Actions** tab ("Run workflow",
the `workflow_dispatch` trigger). A manual run builds and uploads the
per-platform binaries as **workflow artifacts** you can download from the
run page, but it does **not** create a public Release (the publish step only
runs for tag pushes). Use this to confirm a build is green before tagging.

## Notes

- These land under **Releases**, not the repo's **Packages** sidebar.
  GitHub Packages is a package *registry* (Docker via `ghcr.io`, npm, NuGet,
  Maven, Gradle, RubyGems) and does not host raw `.exe`/`.zip` assets —
  Releases is the correct home for downloadable binaries.
- PyInstaller does not cross-compile, which is why each OS is built on its
  own runner. The macOS runner is Apple Silicon (arm64); add an
  `macos-13` matrix entry if you also need an Intel (x86_64) macOS build.
