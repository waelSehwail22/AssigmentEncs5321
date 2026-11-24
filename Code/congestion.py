
#!/usr/bin/env python3

# Mininet Congestion and Queue Management Experiment

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Controller
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
import sys


# 1. Create network topology
class CongestionTopology(Topo):

    def build(self):

        # Create two switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        # Create four hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')
        h3 = self.addHost('h3', ip='10.0.0.3/24')
        h4 = self.addHost('h4', ip='10.0.0.4/24')

        # Create the bottleneck link
        self.addLink(s1, s2)

        # Connect senders
        self.addLink(h1, s1)
        self.addLink(h2, s1)

        # Connect receivers
        self.addLink(h3, s2)
        self.addLink(h4, s2)


# 2. Configure queue management
def configure_queue(sw, interface, mode):

    # Configure bandwidth to 10 Mbps
    sw.cmd(
        f'tc qdisc replace dev {interface} '
        'root handle 1: htb default 10'
    )

    sw.cmd(
        f'tc class replace dev {interface} '
        'parent 1: classid 1:10 htb rate 10mbit ceil 10mbit'
    )

    # FIFO queue
    if mode == 'fifo':
        sw.cmd(
            f'tc qdisc replace dev {interface} '
            'parent 1:10 handle 10: pfifo limit 100'
        )

    # RED queue
    elif mode == 'red':
        sw.cmd(
            f'tc qdisc replace dev {interface} '
            'parent 1:10 handle 10: red '
            'limit 100000 min 30000 max 90000 '
            'avpkt 1000 burst 20 probability 0.02 '
            'bandwidth 10mbit'
        )

    # CoDel queue
    elif mode == 'codel':
        sw.cmd(
            f'tc qdisc replace dev {interface} '
            'parent 1:10 handle 10: codel '
            'limit 1000 target 5ms interval 100ms'
        )


# 3. Start experiment
def run():

    mode = sys.argv[1] if len(sys.argv) > 1 else 'fifo'

    if mode not in ['fifo', 'red', 'codel']:
        print("Choose fifo, red, or codel")
        return

    topo = CongestionTopology()

    net = Mininet(
        topo=topo,
        controller=Controller,
        link=TCLink,
        autoSetMacs=True
    )

    net.start()

    # Configure both ends of the bottleneck link
    configure_queue(net.get('s1'), 's1-eth1', mode)
    configure_queue(net.get('s2'), 's2-eth1', mode)

    print("\n================================")
    print("Congestion Experiment Started")
    print("Queue Management:", mode.upper())
    print("Bottleneck Bandwidth: 10 Mbps")
    print("================================\n")

    # Open Mininet command line
    CLI(net)

    # Stop network
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
