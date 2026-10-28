#!/bin/bash
# Copyright 2017-2019 JetsonHacks
# MIT License
# Create a swap file and set up permissions
# If a parameter is passed, it should be the place to create the swapfile
set -e
SWAPDIRECTORY="/mnt"
# Ubuntu recommends 6GB for 4GB of memory when using suspend
# You can use 1 or 2 if need be
SWAPSIZE=6
AUTOMOUNT="Y"
usage()
{
    echo "usage: installSwapFile [[[-d directory ] [-s size] -a] | [-h]]"
    echo "  -d | --dir <directoryname>   Directory to place swapfile ( default: /mnt)"
    echo "  -s | --size <gigabytes> (default: 6)"
    echo "  -a | --auto  Enable swap on boot in /etc/fstab (default: Y)"
    echo "  -h | --help  This message"
}

while [ "$1" != "" ]; do
    case $1 in
        -d | --dir )            shift
                                SWAPDIRECTORY=$1
                                ;;
        -s | --size )           shift 
				SWAPSIZE=$1
                                ;;
        -a | --auto )           shift
				AUTOMOUNT=$1
				;;
        -h | --help )           usage
                                exit
                                ;;
        * )                     usage
                                exit 1
    esac
    shift
done

echo "Creating Swapfile at: " $SWAPDIRECTORY
echo "Swapfile Size: " $SWAPSIZE"G"
echo "Automount: " $AUTOMOUNT

#Create a swapfile for Ubuntu at the current directory location
sudo fallocate -l $SWAPSIZE"G" $SWAPDIRECTORY"/swapfile"
cd $SWAPDIRECTORY
#List out the file
ls -lh swapfile
# Change permissions so that only root can use it
sudo chmod 600 swapfile
#List out the file
ls -lh swapfile
#Set up the Linux swap area
sudo mkswap swapfile
#Now start using the swapfile
sudo swapon swapfile
#Show that it's now being used
swapon -s

if [ "$AUTOMOUNT" = "Y" ]; then
	echo "Modifying /etc/fstab to enable on boot"
        SWAPLOCATION=$SWAPDIRECTORY"/swapfile"
        echo $SWAPLOCATION
	# Guarded: a second identical swap line makes `mount -a` fail at boot, and
	# an unguarded >> adds one every time this script runs.
	for _lib in "$(dirname "$0")/../upgrades/lib/deploy_common.sh" \
	            "$HOME/flex-run/upgrades/lib/deploy_common.sh"; do
	    if [ -r "$_lib" ]; then
	        . "$_lib"
	        _lib_loaded=1
	        break
	    fi
	done
	if [ -n "${_lib_loaded:-}" ]; then
	    ensure_fstab_entry "$SWAPLOCATION swap swap defaults 0 0"
	elif grep -qE "^[[:space:]]*$SWAPLOCATION[[:space:]]" /etc/fstab; then
	    echo "fstab already has an entry for $SWAPLOCATION - leaving it alone"
	else
	    sudo sh -c 'echo "'$SWAPLOCATION' swap swap defaults 0 0" >> /etc/fstab'
	fi
fi

echo "Swap file has been created"
echo "Reboot to make sure changes are in effect"
