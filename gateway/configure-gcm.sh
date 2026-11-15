#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
    echo 'Usage: configure-gcm.sh [--desktop]' >&2
    exit 2
fi
case "${1:-}" in
    '')
        desktop=false
        ;;
    --desktop)
        # Desktop invokes its bundled GCM itself when the Advanced setting is enabled.
        # Its bundled executable need not be available to command-line Git.
        desktop=true
        ;;
    *)
        echo 'Usage: configure-gcm.sh [--desktop]' >&2
        exit 2
        ;;
esac

prefix=credential.https://devops.kevinc.ltd
if [ "$desktop" = false ]; then
    # Resolve GCM without assuming it is on PATH: GitHub Desktop bundles one at
    # a fixed path and does not add it to the shell search path.
    gcm=${GCM_PATH:-}
    if [ -z "$gcm" ] && command -v git-credential-manager >/dev/null 2>&1; then
        gcm=git-credential-manager
    fi
    if [ -z "$gcm" ]; then
        bundled='/Applications/GitHub Desktop.app/Contents/Resources/app/git/libexec/git-core/git-credential-manager'
        [ -x "$bundled" ] && gcm=$bundled
    fi
    if [ -z "$gcm" ]; then
        echo 'GCM not found; install Git Credential Manager or set GCM_PATH.' >&2
        exit 1
    fi
    "$gcm" --version
    # Git treats a helper value that is not an absolute path as a `git credential-*`
    # subcommand, and a shell helper must quote a path containing spaces.
    case "$gcm" in
        *' '*) gcm="!\"$gcm\"" ;;
    esac
    # Reset inherited helpers for this host only; rerunning does not duplicate entries.
    git config --global --replace-all "$prefix.helper" ''
    git config --global --add "$prefix.helper" "$gcm"
fi
git config --global "$prefix.provider" generic
git config --global "$prefix.oauthClientId" devops-gcm
git config --global "$prefix.oauthAuthorizeEndpoint" /oauth/authorize
git config --global "$prefix.oauthTokenEndpoint" /oauth/token
git config --global "$prefix.oauthRedirectUri" http://127.0.0.1
git config --global "$prefix.oauthScopes" 'git:read git:write'
git config --global "$prefix.oauthDefaultUserName" oauth2
git config --global "$prefix.oauthUseClientAuthHeader" false
echo 'GCM configured for devops.kevinc.ltd.'
if [ "$desktop" = true ]; then
    echo 'In GitHub Desktop, enable Settings > Advanced > Use Git Credential Manager, then retry cloning.'
fi
