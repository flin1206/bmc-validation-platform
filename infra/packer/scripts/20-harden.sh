#!/usr/bin/env bash
# CIS Level 1 oriented hardening for a lab agent. Each block names the control
# family it addresses so audit failures can be traced back to this file.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# --- 1.x  Filesystem / software updates ------------------------------------
apt-get install -y --no-install-recommends unattended-upgrades auditd audispd-plugins \
  apparmor-utils libpam-pwquality aide
dpkg-reconfigure -f noninteractive unattended-upgrades
for fs in cramfs freevxfs hfs hfsplus jffs2 udf usb-storage; do
  echo "install $fs /bin/false" > "/etc/modprobe.d/cis-$fs.conf"
done

# --- 1.6  Mandatory access control ------------------------------------------
aa-enforce /etc/apparmor.d/* 2>/dev/null || true

# --- 2.x  Remove services an agent never needs ------------------------------
apt-get purge -y telnet rsh-client talk nis 2>/dev/null || true
systemctl disable --now avahi-daemon cups 2>/dev/null || true

# --- 3.x  Network kernel parameters -----------------------------------------
cat > /etc/sysctl.d/60-cis.conf <<'EOF'
net.ipv4.ip_forward = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.tcp_syncookies = 1
kernel.randomize_va_space = 2
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
fs.suid_dumpable = 0
EOF

# --- 3.5  Host firewall: SSH in, everything else only outbound --------------
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# --- 4.x  Auditing ----------------------------------------------------------
cat > /etc/audit/rules.d/50-cis.rules <<'EOF'
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/sudoers -p wa -k scope
-w /etc/sudoers.d/ -p wa -k scope
-w /var/log/sudo.log -p wa -k actions
-a always,exit -F arch=b64 -S sethostname,setdomainname -k system-locale
-e 2
EOF
systemctl enable auditd

# --- 5.x  Access, authentication, SSH ---------------------------------------
install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/60-cis.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 4
ClientAliveInterval 300
ClientAliveCountMax 3
LoginGraceTime 60
AllowTcpForwarding no
Banner /etc/issue.net
EOF
chmod 0600 /etc/ssh/sshd_config.d/60-cis.conf
echo "Authorized use only. Activity is logged." | tee /etc/issue /etc/issue.net

sed -i 's/^# *minlen.*/minlen = 14/; s/^# *minclass.*/minclass = 4/' /etc/security/pwquality.conf
sed -i 's/^PASS_MAX_DAYS.*/PASS_MAX_DAYS 365/; s/^UMASK.*/UMASK 027/' /etc/login.defs
echo 'Defaults logfile="/var/log/sudo.log"' > /etc/sudoers.d/60-cis-log
chmod 0440 /etc/sudoers.d/60-cis-log

# --- 6.x  File integrity baseline (taken last, after all changes) ------------
aideinit -y -f || true
