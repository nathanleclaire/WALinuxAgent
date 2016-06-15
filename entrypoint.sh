#!/bin/sh

cp /opt/waagent.conf /etc/waagent.conf
cp /opt/lsb-release /etc/lsb-release

echo '
CHFN_RESTRICT    rwh
DEFAULT_HOME     yes
ENCRYPT_METHOD   SHA512
ENV_PATH         PATH=/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games
ENV_SUPATH       PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ERASECHAR        0177
FAILLOG_ENAB     yes
FTMP_FILE        /var/log/btmp
GID_MAX          60000
GID_MIN          1000
HUSHLOGIN_FILE   .hushlogin
KILLCHAR         025
LOGIN_RETRIES    5
LOGIN_TIMEOUT    60
LOG_OK_LOGINS    no
LOG_UNKFAIL_ENAB no
MAIL_DIR         /var/mail
PASS_MAX_DAYS    99999
PASS_MIN_DAYS    0
PASS_WARN_AGE    7
SU_NAME          su
SYSLOG_SG_ENAB   yes
SYSLOG_SU_ENAB   yes
TTYGROUP         tty
TTYPERM          0600
UID_MAX          60000
UID_MIN          1000
UMASK            022
USERGROUPS_ENAB yes' >/etc/login.defs

/usr/sbin/waagent -daemon -verbose
