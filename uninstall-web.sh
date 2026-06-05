#!/usr/bin/env bash
set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "请使用 root 身份运行"; exit 1; }

systemctl stop host-nginx-manager-web.service 2>/dev/null || true
systemctl disable host-nginx-manager-web.service 2>/dev/null || true
rm -f /etc/systemd/system/host-nginx-manager-web.service
systemctl daemon-reload
rm -rf /opt/host-nginx-manager

echo "Web UI 已卸载。保留 /usr/local/sbin/host-nginx-manager 和 /etc/host-nginx-manager，如需彻底删除请手动移除。"