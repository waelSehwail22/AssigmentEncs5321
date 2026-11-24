#!/usr/bin/env python3
"""
task3_topo.py  -  ENCS5321 Assignment #1, Task 3 (Mininet side)
Topology with redundant paths between h1 and h2:
                 s2
               /    \\              Primary path : h1-s1-s2-s3-h2      (2 links)
     h1 --- s1        s3 --- h2
               \\    /              Backup  path : h1-s1-s4-s5-s3-h2   (3 links)
               s4--s5
All switch-switch links: 10 Mb/s, 2 ms delay -> the backup path has a larger RTT.
Usage (start the POX controller FIRST):
  sudo python3 task3_topo.py              # link down  -> detected by PortStatus
  sudo python3 task3_topo.py --silent     # silent failure -> detected by LLDP timeout
  sudo python3 task3_topo.py --cli-only   # only build the network and open the CLI
"""
import argparse, re, time
from functools import partial
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

PING_FILE = '/tmp/task3_ping.txt'


def now():
    """Date + time with milliseconds (for the screenshots / report)."""
    t = time.time()
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t)) + '.%03d' % (int(t * 1000) % 1000)


class RedundantTopo(Topo):
    def build(self):
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        s1, s2, s3, s4, s5 = [self.addSwitch('s%d' % i) for i in range(1, 6)]
        sw = dict(bw=10, delay='2ms')       # switch-to-switch link parameters
        self.addLink(h1, s1, bw=10)
        self.addLink(h2, s3, bw=10)
        self.addLink(s1, s2, **sw)          # primary (shortest) path
        self.addLink(s2, s3, **sw)
        self.addLink(s1, s4, **sw)          # backup (redundant) path
        self.addLink(s4, s5, **sw)
        self.addLink(s5, s3, **sw)


def set_link(net, a, b, up, silent):
    """Normal: 'link a b down/up' (ports go down -> PortStatus).
    Silent: ports stay UP but a priority-65535 drop rule discards every packet,
    so only the LLDP timeout can detect it."""
    if not silent:
        net.configLinkStatus(a, b, 'up' if up else 'down')
        return
    link = net.linksBetween(net.get(a), net.get(b))[0]
    for intf in (link.intf1, link.intf2):
        sw, port = intf.node, intf.node.ports[intf]
        if up:
            sw.cmd('ovs-ofctl --strict del-flows %s "priority=65535,in_port=%d"' % (sw.name, port))
        else:
            sw.cmd('ovs-ofctl add-flow %s "priority=65535,in_port=%d,actions=drop"' % (sw.name, port))


def show_flows(net, title):
    info('\n*** %s  [%s]\n' % (title, now()))
    for name in ('s1', 's2', 's4'):
        out = net.get(name).cmd('ovs-ofctl dump-flows %s | grep dl_dst' % name)
        info('--- %s ---\n%s' % (name, out))


def analyse(interval, t_fail, t_restore):
    """Parse 'ping -D' output: loss, recovery time and RTT per path."""
    text = open(PING_FILE).read()
    rep = [(float(t), int(s), float(r)) for t, s, r in
           re.findall(r'\[(\d+\.\d+)\][^\n]*icmp_seq=(\d+)[^\n]*time=([\d.]+)', text)]
    if not rep:
        info('*** No ping replies found, check the controller!\n')
        return
    m = re.search(r'(\d+) packets transmitted, (\d+) received', text)
    sent = int(m.group(1)) if m else max(s for _, s, _ in rep)
    got = {s for _, s, _ in rep}
    lost = sorted(set(range(1, sent + 1)) - got)
    first_t, first_seq, first_rtt = rep[0]
    t0 = first_t - first_rtt / 1000.0 - (first_seq - 1) * interval
    send_time = lambda seq: t0 + (seq - 1) * interval     # estimated send time
    lost_fail = [s for s in lost if t_fail - interval <= send_time(s) < t_restore]
    lost_rest = [s for s in lost if send_time(s) >= t_restore]
    # first reply sent after the failure = first packet that used the backup path
    after = [r for r in rep if r[0] - r[2] / 1000.0 >= t_fail and r[0] < t_restore]
    rtt_before = [r[2] for r in rep if r[0] < t_fail]
    rtt_backup = [r[2] for r in after]
    avg = lambda x: sum(x) / len(x) if x else float('nan')
    info('\n================== TASK 3 RESULTS ==================\n')
    info('Packets sent / received      : %d / %d\n' % (sent, len(got)))
    info('Total packet loss            : %d packets (%.2f %%)\n' % (len(lost), 100.0 * len(lost) / sent))
    info('Lost during the failure      : %d packets -> outage ~ %.0f ms\n'
         % (len(lost_fail), len(lost_fail) * interval * 1000))
    info('Lost while restoring link    : %d packets\n' % len(lost_rest))
    if lost:
        info('Lost icmp_seq numbers        : %s\n' % lost)
    if after:
        info('Recovery time (failure -> first reply on backup path): %.1f ms\n'
             % ((after[0][0] - t_fail) * 1000))
    info('Avg RTT before failure (primary s1-s2-s3) : %.2f ms\n' % avg(rtt_before))
    info('Avg RTT during failure (backup s1-s4-s5-s3): %.2f ms\n' % avg(rtt_backup))
    info('====================================================\n')


def run_experiment(net, silent, interval=0.1):
    h1, h2 = net.get('h1', 'h2')
    # LLDP timeout is ~10 s, so the silent failure must last longer
    fail_at, restore_at, total = (5, 15, 25) if not silent else (5, 25, 40)
    info('*** Warm-up ping (controller learns the hosts)\n')
    net.ping([h1, h2])
    show_flows(net, 'Flow tables BEFORE failure (primary path)')
    count = int(total / interval)
    h1.cmd('rm -f %s' % PING_FILE)
    h1.cmd('ping -D -i %s -c %d %s > %s 2>&1 &' % (interval, count, h2.IP(), PING_FILE))
    info('\n*** [%s] Continuous ping h1 -> h2 started (%d packets, every %.1f s)\n'
         % (now(), count, interval))
    time.sleep(fail_at)
    t_fail = time.time()
    set_link(net, 's1', 's2', up=False, silent=silent)
    info('*** [%s] LINK s1-s2 FAILED (%s)\n' % (now(), 'silent' if silent else 'link down'))
    time.sleep(restore_at - fail_at - 1)
    show_flows(net, 'Flow tables DURING failure (backup path)')
    time.sleep(1)
    t_restore = time.time()
    set_link(net, 's1', 's2', up=True, silent=silent)
    info('*** [%s] LINK s1-s2 RESTORED\n' % now())
    time.sleep(total - restore_at + 2)
    h1.cmd('wait')                               # wait for ping to finish
    show_flows(net, 'Flow tables AFTER restoration')
    analyse(interval, t_fail, t_restore)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--silent', action='store_true', help='silent failure (LLDP detection)')
    ap.add_argument('--cli-only', action='store_true', help='only build the network')
    ap.add_argument('--ip', default='127.0.0.1', help='controller IP')
    args = ap.parse_args()
    setLogLevel('info')
    info('*** Started at %s\n' % now())
    net = Mininet(topo=RedundantTopo(),
                  switch=partial(OVSSwitch, protocols='OpenFlow10'),   # POX speaks OF 1.0
                  link=TCLink, controller=None)
    net.addController('c0', controller=RemoteController, ip=args.ip, port=6633)
    net.start()
    for h in net.hosts:                          # less background noise
        h.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null')
    info('*** Waiting 15 s for LLDP topology discovery...\n')
    time.sleep(15)
    if not args.cli_only:
        run_experiment(net, args.silent)
    CLI(net)
    net.stop()
