"""DPER Processor

Copyright (c) 2020 Kirei AB. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:
1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import argparse
import io
import ipaddress
import json
import logging
import os
import re
import shlex
import stat
import subprocess
import sys
import xml.etree.cElementTree as ET
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from tempfile import mkstemp
from typing import Dict, List, Optional

import requests
import voluptuous as vol
import voluptuous.humanize
import yaml
from requests.exceptions import ConnectionError

IP_ADDRESS = ipaddress.ip_address
DOMAIN_NAME = vol.Any(vol.Match(r"\w+"), vol.Match(vol.DOMAIN_REGEX))

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("output_format"): vol.Any("nsd", "knot"),
        vol.Required("output_file"): str,
        "output_diff": bool,
        "cache_dir": vol.IsDir,
        "zonefiles": bool,
        "template": str,
        "acl": str,
        vol.Required("peers"): vol.Schema(
            {
                str: vol.Schema(
                    {
                        vol.Required("source"): vol.FqdnUrl,
                        vol.Required("format"): vol.Any("xml", "json"),
                    }
                ),
            }
        ),
        "reconfigure_command": str,
    }
)

DYNAMIC_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("masters"): vol.Schema(
            [
                {
                    vol.Required("ip"): IP_ADDRESS,
                    vol.Required("tsig"): DOMAIN_NAME,
                }
            ]
        ),
        vol.Required("zones"): vol.Schema([DOMAIN_NAME]),
    }
)

REQUESTS_TIMEOUT = (5, 30)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeerMaster(object):
    ip: str
    tsig: str


@dataclass(frozen=True)
class Peer(object):
    id: str
    masters: List[PeerMaster]
    zones: List[str]


def parse_dynamic_config_dict(peer_id: str, config: dict) -> Peer:
    voluptuous.humanize.validate_with_humanized_errors(config, DYNAMIC_CONFIG_SCHEMA)
    masters = [
        PeerMaster(ip=master["ip"], tsig=master["tsig"]) for master in config["masters"]
    ]
    zones = [re.sub(r"\.$", "", z) for z in config["zones"]]
    logger.debug("Dynamic config for peer %s OK, %d zones", peer_id, len(zones))
    return Peer(id=peer_id, masters=masters, zones=zones)


def parse_dynamic_config_json(peer_id: str, data: str) -> List[Peer]:
    logger.debug("Reading dynamic config for peer %s as JSON", peer_id)
    config = json.loads(data)
    return [parse_dynamic_config_dict(peer_id, config)]


def parse_dynamic_config_xml(peer_id: str, data: str) -> List[Peer]:
    logger.debug("Reading dynamic config for peer %s as XML", peer_id)
    peers = []
    xml_root = ET.fromstring(data)
    for peer in xml_root.findall("./peer"):
        masters = []
        zones = []
        name = peer.attrib["name"]
        for p in peer.findall("./primary"):
            masters.append({"ip": p.text, "tsig": p.attrib["tsig"]})
        for z in peer.findall("./zone"):
            zones.append(z.text)
        config = {"masters": masters, "zones": zones}
        peers.append(parse_dynamic_config_dict(peer_id + "/" + name, config))
    return peers


def check_peers(peers: List[Peer]):
    all_zones: Dict[str, str] = {}
    zone_errors = 0
    for peer in peers:
        for zone in peer.zones:
            if zone in all_zones:
                logger.critical(
                    "zone %s defined by both %s and %s", zone, all_zones[zone], peer.id
                )
                zone_errors += 1
            else:
                all_zones[zone] = peer.id
    if zone_errors > 0:
        raise ValueError("Duplicate zones")


def zone2file(zone: str) -> str:
    return re.sub(r"/", "-", zone.lower())


def generate_nsd(peers: List[Peer], use_zonefiles: bool = False):
    for peer in peers:
        for z in peer.zones:
            print(f"# {peer.id}")
            print("zone:")
            print(f"  name: {z}")
            if use_zonefiles:
                print(f"  zonefile: {zone2file(z)}")
            for m in peer.masters:
                print(f"  allow-notify: {m.ip} {m.tsig}")
                print(f"  allow-notify: {m.ip} NOKEY")
                print(f"  request-xfr: {m.ip} {m.tsig}")
            print("")


def generate_knot(
    peers: List[Peer], template: Optional[str] = None, acl: Optional[str] = None
):

    for peer in peers:

        masters = []
        acls = [acl] if acl else []
        n = 1
        print("remote:")
        for m in peer.masters:
            remote = f"{peer.id}/{n}"
            n += 1
            print("  - id:", remote)
            print("    address:", m.ip)
            if m.tsig:
                print("    key:", m.tsig)
            masters.append(remote)

        print("acl:")
        for m in masters:
            print("  - id:", m)
            print("    remote:", m)
            print("    action: [notify,transfer]")
            acls.append(remote)

        print("zone:")
        for z in peer.zones:
            print("  - domain:", z)
            if template:
                print("    template:", template)
            print("    master:", "[" + ",".join(masters) + "]")
            print("    acl:", "[" + ",".join(acls) + "]")


def get_unless_modified(
    url: str, modified: Optional[datetime]
) -> Optional[requests.Response]:
    headers = {}
    if modified is not None:
        headers["If-Modified-Since"] = modified.strftime("%a, %d %b %Y %H:%M:%S GMT")
    response = requests.get(url, headers=headers, timeout=REQUESTS_TIMEOUT)
    logger.debug("GET %s returned %d", url, response.status_code)
    if response.status_code == 304:
        logger.debug("%s not modified since %s", url, modified)
        return None
    return response


def process_dper(
    peer_id: str,
    source: str,
    cache: Optional[str],
    force_cache: bool,
    payload_format: str,
) -> List[Peer]:
    if cache is not None:
        modified: Optional[datetime] = None
        try:
            modified = datetime.fromtimestamp(os.stat(cache).st_mtime, tz=timezone.utc)
        except FileNotFoundError:
            pass

        try:
            response = None if force_cache else get_unless_modified(source, modified)
        except ConnectionError as exc:
            logger.error("Connection to %s failed: %s", peer_id, str(exc))
            logger.warning("Reverting to cached data for %s", peer_id)
            response = None

        if response is None:
            with open(cache, "rt") as cache_file:
                payload = cache_file.read()
        else:
            response.raise_for_status()
            payload = response.text
            with open(cache, "wt") as cache_file:
                cache_file.write(payload)
    else:
        response = requests.get(source, timeout=REQUESTS_TIMEOUT)
        response.raise_for_status()
        payload = response.text

    if payload_format == "json":
        return parse_dynamic_config_json(peer_id, payload)
    elif payload_format == "xml":
        return parse_dynamic_config_xml(peer_id, payload)
    else:
        raise ValueError("Invalid format: " + payload_format)


def save_config(
    peers: List[Peer],
    filename: str,
    output_format: str = "nsd",
    use_zonefiles: bool = False,
    force_output: bool = False,
    diff: bool = False,
    template: Optional[str] = None,
    acl: Optional[str] = None,
) -> bool:
    config_output = io.StringIO()
    with redirect_stdout(config_output):
        if output_format == "nsd":
            generate_nsd(peers, use_zonefiles)
        elif output_format == "knot":
            generate_knot(peers, template, acl)
        else:
            raise ValueError("Invalid output format")
    output_file, output_path = mkstemp(prefix="conf.", suffix=".tmp", dir=".")
    os.write(output_file, config_output.getvalue().encode())
    os.close(output_file)
    os.chmod(output_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    args = ["diff", "-u", filename, output_path]
    logging.debug("Running diff: %s", " ".join(args))
    res = subprocess.run(args, stdout=subprocess.PIPE, text=True)
    logging.debug("diff returned %d", res.returncode)
    if res.returncode == 0 and not force_output:
        os.remove(output_path)
        logging.info("No change")
        return False

    if res.stdout:
        for line in res.stdout.splitlines():
            logging.info("diff: %s", line.rstrip())

    os.rename(output_path, filename)
    logger.info("Wrote output to %s", filename)
    return True


def main() -> None:
    """Command line tool"""
    parser = argparse.ArgumentParser(description="DPER")

    parser.add_argument(
        "--config",
        dest="config",
        default="dper.yaml",
        metavar="filename",
        help="Configuraton file",
    )
    parser.add_argument(
        "--offline", dest="offline", action="store_true", help="Offline (force cache)"
    )
    parser.add_argument(
        "--force", dest="force", action="store_true", help="Force update"
    )
    parser.add_argument(
        "--debug", dest="debug", action="store_true", help="Enable debugging"
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    with open(args.config) as config_file:
        config = yaml.safe_load(config_file)
    voluptuous.humanize.validate_with_humanized_errors(config, CONFIG_SCHEMA)

    peers = []
    for peer_id, peer_config in config.get("peers", {}).items():
        ext = peer_config["format"]
        if config.get("cache_dir") is not None:
            cache = os.path.join(config.get("cache_dir"), f"{peer_id}.{ext}")
        else:
            cache = None

        try:
            p = process_dper(
                peer_id=peer_id,
                source=peer_config["source"],
                cache=cache,
                force_cache=args.offline,
                payload_format=peer_config["format"],
            )
        except ConnectionError as exc:
            logger.error("Connection to %s failed: %s", peer_id, str(exc))
            sys.exit(-1)

        peers.extend(p)

    check_peers(peers)
    changed = save_config(
        peers=peers,
        filename=config["output_file"],
        output_format=config["output_format"],
        use_zonefiles=config.get("zonefiles", True),
        force_output=args.force,
        diff=config.get("output_diff", False),
        template=config.get("template"),
        acl=config.get("acl"),
    )

    reconfigure = config.get("reconfigure_command")
    if changed and reconfigure:
        logging.info("reconfiguring using %s", reconfigure)
        args = shlex.split(reconfigure)
        res = subprocess.run(args, stdout=subprocess.PIPE, text=True)
        if res.returncode:
            logging.error("reconfigure_command returned non-zero")
            log_func = logging.warning
        else:
            log_func = logging.info
        if res.stdout:
            for line in res.stdout.splitlines():
                log_func("reconfigure: %s", line.rstrip())


if __name__ == "__main__":
    main()
