# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
from pathlib import Path
import json

from west.commands import WestCommand
from west import log

from zap_common import DEFAULT_MATTER_PATH, DEFAULT_ZCL_JSON_RELATIVE_PATH


def add_cluster_to_zcl(zcl_base: Path, cluster_xml_paths: list, output: Path):
    """
    Add the cluster to the ZCL file.
    """

    try:
        with open(zcl_base, "r") as zcl_json_base:
            zcl_json = json.load(zcl_json_base)
    except IOError as e:
        raise RuntimeError(f"No such ZCL file: {zcl_base}")

    # If the output file is provided, we would like to generate a new ZCL file, so we must set
    # the relative paths from the xml.file to the data model directories and manufacturers.xml file.
    # It is because the base zcl.json file contains the relative paths to itself inside as the "xmlRoot",
    # and if we create a new zcl file outside the data model directory it will not work properly.
    if output:
        roots_replaced = list()
        replace = False

        # Replace existing paths with the relative to output ones
        for path in zcl_json.get("xmlRoot"):
            path = zcl_base.parent.joinpath(Path(path))
            if not path == "./" and not path == "." and not path.is_relative_to(output.parent):
                roots_replaced.append(str(path.relative_to(output.parent, walk_up=True)))
                replace = True
        if replace:
            zcl_json["xmlRoot"] = roots_replaced

        # Add the relative path to manufacturers XML
        manufacturers_xml = zcl_base.parent.joinpath(Path(zcl_json.get("manufacturersXml"))).resolve()
        if not manufacturers_xml.parent.is_relative_to(output.parent):
            zcl_json.update({"manufacturersXml": str(manufacturers_xml.relative_to(output.parent, walk_up=True))})

    # Add the new clusters to the ZCL file
    for cluster in cluster_xml_paths:
        if not Path(cluster).exists():
            raise RuntimeError(f"No such cluster file: {cluster}")

        # Get cluster file name
        file = Path(cluster).name
        # Get relative path from the cluster file to the output file.
        relative_path = Path(cluster).absolute().parent.relative_to(output.parent, walk_up=True)

        # We need to add two things:
        # 1. The absolute path to the directory where the new xml file exists to the xmlRoot array.
        # 2. The new xml file name to the xmlFile array.
        if not str(relative_path) in zcl_json.get("xmlRoot"):
            zcl_json.get("xmlRoot").append(str(relative_path))
        if not file in zcl_json.get("xmlFile"):
            zcl_json.get("xmlFile").append(file)
            log.dbg(f"Successfully added {file}")

    # If output file is not provided, we will edit the existing ZCL file
    file_to_write = output if output else zcl_base

    # Save the dumped JSON to the output file
    with open(file_to_write, "w+") as zcl_output:
        zcl_output.write(json.dumps(zcl_json, indent=4))


class ZapAppend(WestCommand):
    def __init__(self):
        super().__init__(
            'zap-append',
            'Add a new custom cluster to the ZCL Matter data model file',
            'A tool for adding a custom cluster to the ZCL Matter Data Model file according to the base ZCL file and custom clusters definitions.')

    def do_add_parser(self, parser_adder) -> argparse.ArgumentParser:
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.description)
        parser.add_argument("-b", "--base", type=Path,
                            help=f"An absolute path to the base zcl.json file. If not provided the path will be set to MATTER/{DEFAULT_ZCL_JSON_RELATIVE_PATH}.")
        parser.add_argument("-m", "--matter", type=Path, default=DEFAULT_MATTER_PATH,
                            help=f"An absolute path to the Matter directory. If not set the path with be set to the {DEFAULT_MATTER_PATH}")
        parser.add_argument("-o", "--output", type=Path,
                            help=f"Output path to store the generated zcl.json file. If not provided the path will be set to the base zcl.json file (MATTER/{DEFAULT_ZCL_JSON_RELATIVE_PATH}).")
        parser.add_argument("new_clusters", nargs='+',
                            help="Paths to the XML files that contain the custom cluster definitions")
        return parser

    def do_run(self, args, unknown_args) -> None:
        if not args.base:
            args.base = args.matter.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH)
        if not args.output:
            args.output = args.matter.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH)

        for cluster in args.new_clusters:
            if not Path(cluster).exists():
                log.err(f"No such cluster file: {cluster}")
                return

        add_cluster_to_zcl(args.base.absolute(), args.new_clusters, args.output.absolute())
