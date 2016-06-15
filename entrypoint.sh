#!/bin/sh

cp /opt/waagent.conf /etc/waagent.conf
cp /opt/lsb-release /etc/lsb-release

/usr/sbin/waagent -daemon -verbose
