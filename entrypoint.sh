#!/bin/sh

cp /opt/waagent.conf /etc/waagent.conf
/usr/sbin/waagent -daemon -verbose
