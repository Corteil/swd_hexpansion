#!/bin/sh
# Capture SWD with the Kingst LA2016 and print the bit stream.
#   capture_swd.sh NAME [SECONDS] [SAMPLERATE]
# Wiring: channel 0 = SWCLK, channel 1 = SWDIO, analyser GND to the common ground.
# Start this, then press Connect on the badge (or run OpenOCD) within the window.
# Writes NAME.sr (open it in PulseView, which has an SWD decoder), then runs swd_bits.py on it.
set -e
NAME=${1:?usage: capture_swd.sh NAME [SECONDS] [SAMPLERATE]}
SECS=${2:-10}
RATE=${3:-4m}
HERE=$(dirname "$0")
sigrok-cli-kingst -d kingst-la2016 --config "samplerate=$RATE" \
    --channels CH0=SWCLK,CH1=SWDIO --triggers SWCLK=r --wait-trigger \
    --time "${SECS}s" -o "$NAME.sr"
python3 -I "$HERE/swd_bits.py" "$NAME.sr"
