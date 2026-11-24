"""
link_failover.py - POX controller for ENCS5321 Assignment #1, Task 3
Link Failure Detection and Automatic Rerouting.
Run:  cd ~/pox && ./pox.py log.level --INFO openflow.discovery link_failover
"""
import time
from collections import deque
from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.ethernet import ethernet

log = core.getLogger()
FLOW_PRIORITY = 100     # priority of the forwarding flows we install
ARP_PRIORITY = 200      # ARP always goes to the controller (host learning)
DISCOVERY_WAIT = 12     # seconds to wait so LLDP discovers all links first


def ts():
    """Wall-clock timestamp with milliseconds."""
    now = time.time()
    return time.strftime("%H:%M:%S", time.localtime(now)) + ".%03d" % (int(now * 1000) % 1000)


def hname(mac):
    """Mininet MAC 00:00:00:00:00:0N -> hN."""
    s = str(mac)
    return "h%d" % int(s[-2:], 16) if s.startswith("00:00:00:00:00:") else s


class LinkFailover(object):

    def __init__(self):
        self.adj = {}      # adj[dpid_a][dpid_b] = port on switch a that leads to b
        self.hosts = {}    # hosts[mac] = (dpid, port)
        self.ready_at = time.time() + DISCOVERY_WAIT
        core.openflow.addListeners(self)            # ConnectionUp, PacketIn, PortStatus
        core.openflow_discovery.addListeners(self)  # LinkEvent (LLDP)
        log.info("[%s] LinkFailover controller started (v2 with ARP learning)", ts())

    # ---------------- helpers ----------------
    def is_link_port(self, dpid, port):
        """True if this port connects to another switch."""
        return port in self.adj.get(dpid, {}).values()

    def bfs_tree(self, root):
        """BFS from the destination switch: parent[s] = next switch from s towards root."""
        parent = {root: None}
        q = deque([root])
        while q:
            u = q.popleft()
            for v in sorted(self.adj):
                if v not in parent and u in self.adj[v]:
                    parent[v] = u
                    q.append(v)
        return parent

    def switch_path(self, src_dpid, dst_dpid):
        parent = self.bfs_tree(dst_dpid)
        if src_dpid not in parent:
            return None
        path = [src_dpid]
        while path[-1] != dst_dpid:
            path.append(parent[path[-1]])
        return path

    def next_port(self, dpid, dst_mac):
        d_dpid, d_port = self.hosts[dst_mac]
        if dpid == d_dpid:
            return d_port
        parent = self.bfs_tree(d_dpid)
        return self.adj[dpid][parent[dpid]] if dpid in parent else None

    # ---------------- path installation ----------------
    def install_paths_to(self, mac):
        """Install/replace the flow for destination mac on EVERY switch."""
        dst_dpid, dst_port = self.hosts[mac]
        parent = self.bfs_tree(dst_dpid)
        for con in core.openflow.connections:
            dpid = con.dpid
            fm = of.ofp_flow_mod()
            fm.match.dl_dst = mac                      # destination-based forwarding
            fm.priority = FLOW_PRIORITY
            if dpid == dst_dpid:
                out = dst_port                         # last switch: deliver to host
            elif dpid in parent:
                out = self.adj[dpid][parent[dpid]]     # next hop towards the host
            else:
                fm.command = of.OFPFC_DELETE_STRICT     # no path: remove old flow
                con.send(fm)
                continue
            fm.actions.append(of.ofp_action_output(port=out))  # ADD replaces old flow
            con.send(fm)

    def recompute(self, reason):
        t0 = time.time()
        for mac in list(self.hosts):
            self.install_paths_to(mac)
        log.info("[%s] Routes recomputed (%s): %d host(s), took %.2f ms",
                 ts(), reason, len(self.hosts), (time.time() - t0) * 1000)
        self.print_paths()

    def print_paths(self):
        macs = sorted(self.hosts, key=str)
        for a in macs:
            for b in macs:
                if a != b:
                    p = self.switch_path(self.hosts[a][0], self.hosts[b][0])
                    txt = " -> ".join("s%d" % d for d in p) if p else "NO PATH"
                    log.info("      path %s -> %s : %s", hname(a), hname(b), txt)

    # ---------------- OpenFlow events ----------------
    def _handle_ConnectionUp(self, event):
        self.adj.setdefault(event.dpid, {})
        self.ready_at = max(self.ready_at, time.time() + DISCOVERY_WAIT)
        # send ALL ARP to the controller so every host gets learned
        fm = of.ofp_flow_mod()
        fm.priority = ARP_PRIORITY
        fm.match.dl_type = ethernet.ARP_TYPE
        fm.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
        event.connection.send(fm)
        log.info("[%s] Switch s%d connected", ts(), event.dpid)

    def _handle_ConnectionDown(self, event):
        dpid = event.dpid
        self.adj.pop(dpid, None)
        for nbrs in self.adj.values():
            nbrs.pop(dpid, None)
        for mac in [m for m, loc in self.hosts.items() if loc[0] == dpid]:
            del self.hosts[mac]
        log.warning("[%s] Switch s%d disconnected", ts(), dpid)
        self.recompute("switch s%d down" % dpid)

    def _handle_LinkEvent(self, event):
        """LLDP (openflow.discovery): link added / removed after timeout."""
        l = event.link
        if event.added:
            if self.adj.setdefault(l.dpid1, {}).get(l.dpid2) != l.port1:
                self.adj[l.dpid1][l.dpid2] = l.port1
                for mac in [m for m, loc in self.hosts.items() if loc == (l.dpid1, l.port1)]:
                    del self.hosts[mac]                # a switch port is not a host port
                log.info("[%s] LLDP: link UP   s%d:%d -> s%d:%d",
                         ts(), l.dpid1, l.port1, l.dpid2, l.port2)
                self.recompute("link added")
        elif event.removed:
            if self.adj.get(l.dpid1, {}).get(l.dpid2) == l.port1:
                del self.adj[l.dpid1][l.dpid2]
                log.warning("[%s] LINK FAILURE detected by LLDP timeout: s%d:%d -> s%d:%d",
                            ts(), l.dpid1, l.port1, l.dpid2, l.port2)
                self.recompute("LLDP link timeout")

    def _handle_PortStatus(self, event):
        """Fast detection: the switch reports that a port went down / up."""
        desc = event.ofp.desc
        dpid, port = event.dpid, event.port
        down = (event.deleted or (desc.config & of.OFPPC_PORT_DOWN) or
                (desc.state & of.OFPPS_LINK_DOWN))
        if down:
            removed = False
            for nbr, p in list(self.adj.get(dpid, {}).items()):
                if p == port:
                    del self.adj[dpid][nbr]                 # a -> b
                    self.adj.get(nbr, {}).pop(dpid, None)    # b -> a
                    removed = True
                    log.warning("[%s] LINK FAILURE detected by PortStatus: s%d port %d (link s%d <-> s%d)",
                                ts(), dpid, port, dpid, nbr)
            if removed:
                self.recompute("link failure")
        else:
            # port is up again: restore the link if LLDP still knows it
            restored = False
            for l in list(core.openflow_discovery.adjacency):
                if (l.dpid1, l.port1) == (dpid, port) or (l.dpid2, l.port2) == (dpid, port):
                    if self.adj.setdefault(l.dpid1, {}).get(l.dpid2) != l.port1:
                        self.adj[l.dpid1][l.dpid2] = l.port1
                        restored = True
                    if self.adj.setdefault(l.dpid2, {}).get(l.dpid1) != l.port2:
                        self.adj[l.dpid2][l.dpid1] = l.port2
                        restored = True
            if restored:
                log.info("[%s] Link restored: s%d port %d is UP", ts(), dpid, port)
                self.recompute("link restored")

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed or packet.type == ethernet.LLDP_TYPE:
            return                                   # LLDP is handled by discovery
        if time.time() < self.ready_at:
            return                                   # topology not discovered yet
        dpid, inport = event.dpid, event.port
        src, dst = packet.src, packet.dst
        if self.is_link_port(dpid, inport):
            return                                   # already handled at ingress (no loops)
        # 1) learn the host location and install paths towards it
        if not src.is_multicast and self.hosts.get(src) != (dpid, inport):
            self.hosts[src] = (dpid, inport)
            log.info("[%s] Host %s (%s) learned at s%d port %d",
                     ts(), hname(src), src, dpid, inport)
            self.install_paths_to(src)
            self.print_paths()
        # 2) forward this packet
        if dst.is_multicast or dst not in self.hosts:
            self.flood_to_edges(event)
        elif packet.type == ethernet.ARP_TYPE:
            d_dpid, d_port = self.hosts[dst]         # deliver ARP directly to the host
            con = core.openflow.getConnection(d_dpid)
            if con:
                msg = of.ofp_packet_out(in_port=of.OFPP_NONE, data=event.data)
                msg.actions.append(of.ofp_action_output(port=d_port))
                con.send(msg)
        else:
            out = self.next_port(dpid, dst)
            if out is None:
                return                               # no path at the moment -> drop
            msg = of.ofp_packet_out(in_port=inport)
            msg.data = event.ofp                     # uses the switch buffer if any
            msg.actions.append(of.ofp_action_output(port=out))
            event.connection.send(msg)

    def flood_to_edges(self, event):
        """Loop-free flooding: only host-facing ports of all switches."""
        for con in core.openflow.connections:
            ports = [p.port_no for p in con.features.ports
                     if p.port_no < of.OFPP_MAX
                     and not self.is_link_port(con.dpid, p.port_no)
                     and not (con.dpid == event.dpid and p.port_no == event.port)]
            if not ports:
                continue
            if con.dpid == event.dpid:
                msg = of.ofp_packet_out(in_port=event.port)
                msg.data = event.ofp
            else:
                msg = of.ofp_packet_out(in_port=of.OFPP_NONE)
                msg.data = event.data
            for p in ports:
                msg.actions.append(of.ofp_action_output(port=p))
            con.send(msg)


def launch():
    """Called by POX after openflow and openflow.discovery are ready."""
    def start():
        core.registerNew(LinkFailover)
    core.call_when_ready(start, ["openflow", "openflow_discovery"])
