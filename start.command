#!/usr/bin/env bash

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
"$script_dir/start.sh"

echo
read -r -p "WaySplit is open. Press Return to close this window. " _
