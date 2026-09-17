# Nothing built the image that deploys

`CLAUDE.md` calls `.github/workflows/checks.yml` **the single gate**, and it is
— for the Hub as a Python program. It installs `requirements.txt` onto an
Ubuntu runner through `setup-python` and runs every check a contributor runs.

That is not what ships. What ships is the `Dockerfile`: a different base image,
`apt` packages (Ghostscript, qPDF, ffmpeg, Chromium, fonts), Node 20, **three**
`npm ci` runs and **two** TypeScript builds. Grep every workflow in the repo for
`docker` and the only hits are comments *about* the Dockerfile. Nothing built
it. Nothing ran it.

So a change that breaks the deployed artifact was green on the gate and failed
on Render — the shape this guide warns about in its own words, a merged change
sitting dead in production with every screen reporting success.

## The version skew is the sharp end

`checks.yml` pins `python-version: '3.12'`. The Dockerfile is
`FROM python:3.12-slim`. They agree today, and **nothing made them agree** —
they are two numbers in two files that a person kept in step.

With an open Dependabot PR moving the base to `python:3.14-slim`, that stops
being theoretical. A green run on that PR is evidence that **3.12** still
works. It says nothing whatever about the 3.14 the container would run, and
there is no screen anywhere that would say so.

`test_ci_gate.py` refuses the skew now. It reads the `FROM python:X.Y` line and
the `python-version:` line and requires them to match. Driven against the real
scenario rather than argued:

| | |
|---|---|
| bump the Dockerfile alone (the PR as it stands) | **red**, naming both versions |
| bump both | **green** |

The second row is the point. This does not block the upgrade — it requires that
the gate test what the image ships.

## What the build job does, and what it does not

It builds the image, then runs one command inside it:

```
python -c "import wsgi; assert wsgi.application"
```

**An import smoke test, deliberately not a boot.** `docker-start.sh` brings up
gunicorn and two Node services; polling that in CI buys realism and a flake
rate. The failures this exists to catch — a missing system library, a
requirement that will not install on the base, a module that cannot import on
this Python — all surface at the import. Saying it "proves the container serves
traffic" would be a claim nobody checked, so it is not said.

## Path-filtered, and the cost is stated

Every expensive layer sits **before** `COPY . .` — apt, pip, three npm
installs. A Python-only change cannot break the build and should not pay for
one. The job runs only when `Dockerfile`, `docker-start.sh`, `requirements.txt`,
`.dockerignore`, one of the three `package*.json` pairs, or `checks.yml` itself
changes.

**When it cannot tell, it builds.** A first push to a branch, a force-push and
a `workflow_dispatch` all leave no resolvable base commit, and the two outcomes
are not weighed equally: a build that runs unnecessarily costs runner minutes,
and one skipped when it was needed is the exact failure the job exists to
prevent.

The filter is asserted in `test_ci_gate.py` rather than trusted, because a
filter that quietly stops naming a file is a job that quietly stops running.
That check found its own bug first: the slice took `"  image:"`, which also
matches the **postgres service's** `image:` key ninety lines up, so it read the
wrong region of the file and reported `docker-start.sh` as missing from a
filter it was in.

## What this could not verify

There is no Docker daemon in the sandbox this was written in, so **the build
was never run locally**. That is not left to be discovered: the path filter
includes `checks.yml`, so the job runs on the very pull request that adds it.
The first thing it does is build the image, and the PR cannot go green without
it.

## Reported and not fixed: there is no `.dockerignore`

`COPY . .` copies the whole tree. Measured on this checkout: **371 MB**, of
which **30 MB is `.git`**, plus `modules/ad_builder/node_modules` and
`modules/marketing_audit/node_modules` where a developer has built locally.

The second one matters more than the size. `COPY . .` runs **after** the `npm
ci` layers, so a local build copies a host-built `node_modules` — with host
native binaries for `sharp` — over the one the image just installed. **A local
`docker build` does not produce the image CI builds.** CI is unaffected, since
a fresh checkout has no `node_modules`.

Not fixed here on purpose: a `.dockerignore` changes the artifact that deploys,
and this change is about building the artifact that exists rather than altering
it. It wants its own change and its own verification.

## Render environment

Nothing. This is a CI job and a consistency check; the service reads no
variable because of it, and the image it builds is thrown away.
