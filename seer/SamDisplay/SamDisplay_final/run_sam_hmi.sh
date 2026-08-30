#!/bin/bash

cd /home/mic-711/Desktop/SamDisplay

sleep 60

export PYTHONPATH=/home/mic-711/Desktop/SamDisplay:$PYTHONPATH

export DISPLAY=:0

/usr/bin/python3 -m sam_hmi.sam_hmi_6
