
#!/usr/bin/env python3

# Import required Mininet libraries
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Controller
from mininet.cli import CLI
from mininet.log import setLogLevel

# Define our custom network topology
class CustomTopology(Topo):

    def build(self):

        # Create switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        # Create hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')
        h3 = self.addHost('h3', ip='10.0.0.3/24')
        h4 = self.addHost('h4', ip='10.0.0.4/24')

        # Connect switches together
        self.addLink(s1, s2)
        self.addLink(s1, s3)

        # Connect hosts to switches
        self.addLink(h1, s2)
        self.addLink(h2, s2)
        self.addLink(h3, s3)
        self.addLink(h4, s3)


# Main function
def run():

    topo = CustomTopology()

    # Create Mininet network
    net = Mininet(
        topo=topo,
        controller=Controller
    )

    # Start network
    net.start()

    print("Custom topology started successfully!")

    # Open Mininet CLI
    CLI(net)

    # Stop network
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
