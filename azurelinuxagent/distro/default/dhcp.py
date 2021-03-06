# Microsoft Azure Linux Agent
#
# Copyright 2014 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Requires Python 2.4+ and Openssl 1.0+
import os
import socket
import array
import time
import threading
import azurelinuxagent.logger as logger
import azurelinuxagent.conf as conf
import azurelinuxagent.utils.fileutil as fileutil
import azurelinuxagent.utils.shellutil as shellutil
from azurelinuxagent.utils.textutil import hex_dump, hex_dump2, hex_dump3, \
                                           compare_bytes, str_to_ord, \
                                           unpack_big_endian, \
                                           unpack_little_endian, \
                                           int_to_ip4_addr
from azurelinuxagent.exception import DhcpError


class DhcpHandler(object):
    """
    Azure use DHCP option 245 to pass endpoint ip to VMs.
    """
    def __init__(self, distro):
        self.distro = distro
        self.endpoint = None
        self.gateway = None
        self.routes = None

    def run(self):
        """
        Send dhcp request
        Configure default gateway and routes
        Save wire server endpoint if found
        """
        self.send_dhcp_req()
        self.conf_routes()

    def wait_for_network(self):
        """
        Wait for network stack to be initialized.
        """
        ipv4 = self.distro.osutil.get_ip4_addr()
        while ipv4 == '' or ipv4 == '0.0.0.0':
            logger.info("Waiting for network.")
            time.sleep(10)
            logger.info("Try to start network interface.")
            self.distro.osutil.start_network()
            ipv4 = self.distro.osutil.get_ip4_addr()

    def conf_routes(self):
        logger.info("Configure routes")
        logger.info("Gateway:{0}", self.gateway)
        logger.info("Routes:{0}", self.routes)
        #Add default gateway
        if self.gateway is not None:
            self.distro.osutil.route_add(0 , 0, self.gateway)
        if self.routes is not None:
            for route in self.routes:
                self.distro.osutil.route_add(route[0], route[1], route[2])

    def _send_dhcp_req(self, request):    
        __waiting_duration__ = [0, 10, 30, 60, 60]
        for duration in __waiting_duration__:
            try:
                self.distro.osutil.allow_dhcp_broadcast()
                response = socket_send(request)
                validate_dhcp_resp(request, response)
                return response
            except DhcpError as e:
                logger.warn("Failed to send DHCP request: {0}", e)
            time.sleep(duration)
        return None

    def send_dhcp_req(self):
        """
        Build dhcp request with mac addr
        Configure route to allow dhcp traffic
        Stop dhcp service if necessary
        """
        logger.info("Send dhcp request")
        mac_addr = self.distro.osutil.get_mac_addr()
        req = build_dhcp_request(mac_addr)

        # Temporary allow broadcast for dhcp. Remove the route when done.
        missing_default_route = self.distro.osutil.is_missing_default_route()
        ifname = self.distro.osutil.get_if_name()
        if missing_default_route:
            self.distro.osutil.set_route_for_dhcp_broadcast(ifname)

        # In some distros, dhcp service needs to be shutdown before agent probe
        # endpoint through dhcp.
        if self.distro.osutil.is_dhcp_enabled():
            self.distro.osutil.stop_dhcp_service()

        resp = self._send_dhcp_req(req)
        
        if self.distro.osutil.is_dhcp_enabled():
            self.distro.osutil.start_dhcp_service()

        if missing_default_route:
            self.distro.osutil.remove_route_for_dhcp_broadcast(ifname)

        if resp is None:
            raise DhcpError("Failed to receive dhcp response.")
        self.endpoint, self.gateway, self.routes = parse_dhcp_resp(resp)

def validate_dhcp_resp(request, response):
    bytes_recv = len(response)
    if bytes_recv < 0xF6:
        logger.error("HandleDhcpResponse: Too few bytes received:{0}",
                     bytes_recv)
        return False

    logger.verb("BytesReceived:{0}", hex(bytes_recv))
    logger.verb("DHCP response:{0}", hex_dump(response, bytes_recv))

    # check transactionId, cookie, MAC address cookie should never mismatch
    # transactionId and MAC address may mismatch if we see a response
    # meant from another machine
    if not compare_bytes(request, response, 0xEC, 4):
        logger.verb("Cookie not match:\nsend={0},\nreceive={1}",
                       hex_dump3(request, 0xEC, 4),
                       hex_dump3(response, 0xEC, 4))
        raise DhcpError("Cookie in dhcp respones doesn't match the request")

    if not compare_bytes(request, response, 4, 4):
        logger.verb("TransactionID not match:\nsend={0},\nreceive={1}",
                       hex_dump3(request, 4, 4),
                       hex_dump3(response, 4, 4))
        raise DhcpError("TransactionID in dhcp respones "
                            "doesn't match the request")

    if not compare_bytes(request, response, 0x1C, 6):
        logger.verb("Mac Address not match:\nsend={0},\nreceive={1}",
                       hex_dump3(request, 0x1C, 6),
                       hex_dump3(response, 0x1C, 6))
        raise DhcpError("Mac Addr in dhcp respones "
                            "doesn't match the request")

def parse_route(response, option, i, length, bytes_recv):
    # http://msdn.microsoft.com/en-us/library/cc227282%28PROT.10%29.aspx
    logger.verb("Routes at offset: {0} with length:{1}", hex(i), hex(length))
    routes = []
    if length < 5:
        logger.error("Data too small for option:{0}", option)
    j = i + 2
    while j < (i + length + 2):
        mask_len_bits = str_to_ord(response[j])
        mask_len_bytes = (((mask_len_bits + 7) & ~7) >> 3)
        mask = 0xFFFFFFFF & (0xFFFFFFFF << (32 - mask_len_bits))
        j += 1
        net = unpack_big_endian(response, j, mask_len_bytes)
        net <<= (32 - mask_len_bytes * 8)
        net &= mask
        j += mask_len_bytes
        gateway = unpack_big_endian(response, j, 4)
        j += 4
        routes.append((net, mask, gateway))
    if j != (i + length + 2):
        logger.error("Unable to parse routes")
    return routes

def parse_ip_addr(response, option, i, length, bytes_recv):
    if i + 5 < bytes_recv:
        if length != 4:
            logger.error("Endpoint or Default Gateway not 4 bytes")
            return None
        addr = unpack_big_endian(response, i + 2, 4)
        ip_addr = int_to_ip4_addr(addr)
        return ip_addr
    else:
        logger.error("Data too small for option:{0}", option)
    return None

def parse_dhcp_resp(response):
    """
    Parse DHCP response:
    Returns endpoint server or None on error.
    """
    logger.verb("parse Dhcp Response")
    bytes_recv = len(response)
    endpoint = None
    gateway = None
    routes = None

    # Walk all the returned options, parsing out what we need, ignoring the
    # others. We need the custom option 245 to find the the endpoint we talk to,
    # as well as, to handle some Linux DHCP client incompatibilities,
    # options 3 for default gateway and 249 for routes. And 255 is end.

    i = 0xF0 # offset to first option
    while i < bytes_recv:
        option = str_to_ord(response[i])
        length = 0
        if (i + 1) < bytes_recv:
            length = str_to_ord(response[i + 1])
        logger.verb("DHCP option {0} at offset:{1} with length:{2}",
                    hex(option), hex(i), hex(length))
        if option == 255:
            logger.verb("DHCP packet ended at offset:{0}", hex(i))
            break
        elif option == 249:
            routes = parse_route(response, option, i, length, bytes_recv)
        elif option == 3:
            gateway = parse_ip_addr(response, option, i, length, bytes_recv)
            logger.verb("Default gateway:{0}, at {1}", gateway, hex(i))
        elif option == 245:
            endpoint = parse_ip_addr(response, option, i, length, bytes_recv)
            logger.verb("Azure wire protocol endpoint:{0}, at {1}", gateway,
                        hex(i))
        else:
            logger.verb("Skipping DHCP option:{0} at {1} with length {2}",
                        hex(option), hex(i), hex(length))
        i += length + 2
    return endpoint, gateway, routes

def socket_send(request):
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                             socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 68))
        sock.sendto(request, ("<broadcast>", 67))
        sock.settimeout(10)
        logger.verb("Send DHCP request: Setting socket.timeout=10, "
                       "entering recv")
        response = sock.recv(1024)
        return response
    except IOError as e:
        raise DhcpError("{0}".format(e))
    finally:
        if sock is not None:
            sock.close()

def build_dhcp_request(mac_addr):
    """
    Build DHCP request string.
    """
    #
    # typedef struct _DHCP {
    #     UINT8   Opcode;                    /* op:    BOOTREQUEST or BOOTREPLY */
    #     UINT8   HardwareAddressType;       /* htype: ethernet */
    #     UINT8   HardwareAddressLength;     /* hlen:  6 (48 bit mac address) */
    #     UINT8   Hops;                      /* hops:  0 */
    #     UINT8   TransactionID[4];          /* xid:   random */
    #     UINT8   Seconds[2];                /* secs:  0 */
    #     UINT8   Flags[2];                  /* flags: 0 or 0x8000 for broadcast */
    #     UINT8   ClientIpAddress[4];        /* ciaddr: 0 */
    #     UINT8   YourIpAddress[4];          /* yiaddr: 0 */
    #     UINT8   ServerIpAddress[4];        /* siaddr: 0 */
    #     UINT8   RelayAgentIpAddress[4];    /* giaddr: 0 */
    #     UINT8   ClientHardwareAddress[16]; /* chaddr: 6 byte eth MAC address */
    #     UINT8   ServerName[64];            /* sname:  0 */
    #     UINT8   BootFileName[128];         /* file:   0  */
    #     UINT8   MagicCookie[4];            /*   99  130   83   99 */
    #                                        /* 0x63 0x82 0x53 0x63 */
    #     /* options -- hard code ours */
    #
    #     UINT8 MessageTypeCode;              /* 53 */
    #     UINT8 MessageTypeLength;            /* 1 */
    #     UINT8 MessageType;                  /* 1 for DISCOVER */
    #     UINT8 End;                          /* 255 */
    # } DHCP;
    #

    # tuple of 244 zeros
    # (struct.pack_into would be good here, but requires Python 2.5)
    request = [0] * 244

    trans_id = gen_trans_id()

    # Opcode = 1
    # HardwareAddressType = 1 (ethernet/MAC)
    # HardwareAddressLength = 6 (ethernet/MAC/48 bits)
    for a in range(0, 3):
        request[a] = [1, 1, 6][a]

    # fill in transaction id (random number to ensure response matches request)
    for a in range(0, 4):
        request[4 + a] = str_to_ord(trans_id[a])

    logger.verb("BuildDhcpRequest: transactionId:%s,%04X" % (
                   hex_dump2(trans_id),
                   unpack_big_endian(request, 4, 4)))

    # fill in ClientHardwareAddress
    for a in range(0, 6):
        request[0x1C + a] = str_to_ord(mac_addr[a])

    # DHCP Magic Cookie: 99, 130, 83, 99
    # MessageTypeCode = 53 DHCP Message Type
    # MessageTypeLength = 1
    # MessageType = DHCPDISCOVER
    # End = 255 DHCP_END
    for a in range(0, 8):
        request[0xEC + a] = [99, 130, 83, 99, 53, 1, 1, 255][a]
    return array.array("B", request)

def gen_trans_id():
    return os.urandom(4)
