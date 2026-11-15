#!/bin/sh
set -eu
# Pin image-owned hooks for existing and new repositories. -c is inherited by
# receive-pack and overrides repository config; no repository hook installation.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
# spawn-fcgi drops UID but inherits root's HOME. Git independently reads global
# attributes/excludes through HOME and XDG_CONFIG_HOME even with GLOBAL disabled.
# This image-owned, empty directory is readable and not writable by CGI jobs.
export HOME=/usr/local/share/devops-git-home XDG_CONFIG_HOME=/usr/local/share/devops-git-home
# git init --shared sets denyNonFastForwards=true. The owner-controlled hook is
# authoritative; otherwise turning push protection off would have no effect.
exec git -c core.hooksPath=/usr/local/lib/devops-git-hooks \
    -c receive.denyNonFastForwards=false http-backend
