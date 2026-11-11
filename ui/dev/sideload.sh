#!/bin/sh
# Builds Valve Oil UI and swaps it into ~/tltg-valve-oil-ui on a printer for testing. It only replaces those files:
# no service restarts and no nginx changes, so the printer must already serve /valve-oil/ (an install with Valve Oil
# UI does). Open tabs reload themselves within five minutes, or on refresh.
#
#   dev/sideload.sh qidi@<printer-ip>
#
# The marker reads "sideload" rather than an archive checksum, so the next real install replaces this build.
set -eu
cd "$(dirname "$0")/.."
host=${1:?usage: dev/sideload.sh qidi@<printer-ip>}
version=$(sed -n 's/^  version: "\(.*\)"$/\1/p' ../installer/package.yaml | head -1)

VO_UI_VERSION="$version+sideload.$(date +%Y%m%d%H%M%S)" npm run build >/dev/null
ssh -o BatchMode=yes "$host" 'grep -q "tltg-optimized:valve-oil-ui" /etc/nginx/sites-available/fluidd' ||
  { echo "The printer's nginx site does not serve /valve-oil/; install the package with Valve Oil UI first." >&2; exit 1; }
ssh -o BatchMode=yes "$host" 'rm -rf ~/tltg-valve-oil-ui.sideload-staging && mkdir ~/tltg-valve-oil-ui.sideload-staging'
scp -q -r -o BatchMode=yes dist/. "$host:tltg-valve-oil-ui.sideload-staging/"
ssh -o BatchMode=yes "$host" 'set -e; cd ~
echo sideload > tltg-valve-oil-ui.sideload-staging/.tltg-optimized-valve-oil-ui
chmod -R a+rX tltg-valve-oil-ui.sideload-staging
rm -rf tltg-valve-oil-ui.sideload-old
if [ -e tltg-valve-oil-ui ]; then mv tltg-valve-oil-ui tltg-valve-oil-ui.sideload-old; fi
mv tltg-valve-oil-ui.sideload-staging tltg-valve-oil-ui
rm -rf tltg-valve-oil-ui.sideload-old'
echo "Sideloaded $(cat dist/version.json) to ${host#*@}/valve-oil/"
