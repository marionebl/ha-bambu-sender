#!/usr/bin/env bash
# Each Home Assistant app (config.yaml with a slug) needs a CHANGELOG.md
# beside it with a "## <version>" heading for its current version.
set -u
fail=0
while IFS= read -r cfg; do
  grep -q '^slug:' "$cfg" || continue
  dir=$(dirname "$cfg")
  ver=$(sed -n 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' "$cfg")
  log="$dir/CHANGELOG.md"
  if [ ! -f "$log" ]; then
    echo "::error file=$cfg::$log is missing"; fail=1
  elif ! grep -Eq "^## ${ver//./\\.}( |$)" "$log"; then
    echo "::error file=$log::no '## $ver' entry for the version in $cfg"; fail=1
  else
    echo "ok: $dir $ver"
  fi
done < <(git ls-files '*config.yaml')
exit $fail
