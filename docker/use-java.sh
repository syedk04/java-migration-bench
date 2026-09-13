#!/usr/bin/env bash
# Switch the active JDK in the current shell. Must be sourced, not executed:
#   . use-java.sh 8
#   . use-java.sh 17
set -euo pipefail

case "${1:-}" in
  8)
    export JAVA_HOME="${JAVA_8_HOME}"
    ;;
  17)
    export JAVA_HOME="${JAVA_17_HOME}"
    ;;
  *)
    echo "usage: . use-java.sh {8|17}" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

export PATH="${JAVA_HOME}/bin:$(echo "${PATH}" | sed -E 's#[^:]*/jvm/[^:]*:##')"
