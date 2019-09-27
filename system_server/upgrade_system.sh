ARCH=$(arch)
if [ "$ARCH" = "aarch64" ]; then
    sh ../upgrades/arm_system_upgrade.sh
elif [ "$ARCH" = "x86_64" ]; then
    sh ../upgrades/x86_system_upgrade.sh
fi

