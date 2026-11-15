#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
    echo 'Usage: configure-gcm.sh [--desktop]' >&2
    exit 2
fi
case "${1:-}" in
    '')
        # Standalone Git needs GCM installed on its own executable search path.
        git credential-manager --version
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
    # Reset inherited helpers for this host only; rerunning does not duplicate entries.
    git config --global --replace-all "$prefix.helper" ''
    git config --global --add "$prefix.helper" manager
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
