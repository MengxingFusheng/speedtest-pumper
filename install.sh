#!/bin/sh
set -eu

INSTALL_DIR="${INSTALL_DIR:-$HOME/speedtest-pumper}"
REPO_URL="https://github.com/MengxingFusheng/speedtest-pumper.git"
NO_START=0

if [ "${1:-}" = "--no-start" ]; then
  NO_START=1
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "未找到命令 $1，请先安装后再运行此脚本" >&2
    exit 1
  fi
}

require_command git
require_command docker

if [ -d "$INSTALL_DIR" ]; then
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "安装目录已存在但不是 git 仓库: $INSTALL_DIR" >&2
    exit 1
  fi
  echo "更新已有目录: $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "克隆仓库到: $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x ./deploy.sh
if [ "$NO_START" -eq 1 ]; then
  ./deploy.sh --no-start
else
  ./deploy.sh
fi
