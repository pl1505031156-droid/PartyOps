#!/usr/bin/env sh
set -eu

id=unknown
version=unknown
id_like=
if [ -r /etc/os-release ]; then
  # os-release 是发行版定义的键值文件；只读取标准字段，不执行额外命令。
  . /etc/os-release
  id="${ID:-unknown}"
  version="${VERSION_ID:-unknown}"
  id_like="${ID_LIKE:-}"
fi
case "$(uname -m)" in
  x86_64|amd64) architecture=amd64 ;;
  aarch64|arm64) architecture=arm64 ;;
  *) echo "不支持的处理器架构：$(uname -m)" >&2; exit 2 ;;
esac
case "$(printf '%s %s' "$id" "$id_like" | tr '[:upper:]' '[:lower:]')" in
  *openeuler*|*rhel*|*fedora*) package_format=rpm ;;
  *kylin*|*uos*|*uniontech*|*deepin*|*debian*|*ubuntu*) package_format=deb ;;
  *) echo "不支持的 Linux 发行版：$id $version" >&2; exit 2 ;;
esac
printf 'distribution=%s\ndistribution_version=%s\npackage_format=%s\narchitecture=%s\n' \
  "$id" "$version" "$package_format" "$architecture"
