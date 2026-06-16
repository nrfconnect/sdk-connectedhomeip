# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from west import log
from west.commands import WestCommand
from zap_common import DEFAULT_MATTER_PATH, DEFAULT_MATTER_TYPES_RELATIVE_PATH, DEFAULT_ZCL_JSON_RELATIVE_PATH


def add_custom_attributes_from_xml(xml_file: Path, zcl_data: dict, matter_path: Path = DEFAULT_MATTER_PATH):
    """
    Parse the cluster XML file and add attributes with custom types to
    attributeAccessInterfaceAttributes in zcl_data.

    Args:
        cluster_xml_path: Path to the cluster XML file
        zcl_data: The loaded zcl.json data dictionary
        matter_path: Path to the Matter directory
    """

    # Step 1: Load all type names from chip-types.xml into a list
    types = []
    tree = ET.parse(matter_path / DEFAULT_MATTER_TYPES_RELATIVE_PATH)
    root = tree.getroot()

    for type_element in root.findall('.//type'):
        description = type_element.get('name')
        if description:
            types.append(description)

    # Step 2: Parse the cluster XML file
    cluster_tree = ET.parse(xml_file)
    cluster_root = cluster_tree.getroot()

    # Find cluster name and attributes with missing types
    attributes_with_missing_types = []

    for cluster in cluster_root.findall('.//cluster'):
        cluster_name = cluster.find('name')
        if cluster_name is not None:
            cluster_name = cluster_name.text
        else:
            continue

        # Check all attributes in the cluster
        for attribute in cluster.findall('attribute'):
            attr_type = attribute.get('type')
            attr_name = attribute.get('name')

            if attr_type and attr_type not in types:
                attributes_with_missing_types.append({
                    'cluster': cluster_name,
                    'attribute': attr_name,
                    'type': attr_type
                })

    # Step 3: Update zcl_data with missing attributes
    if 'attributeAccessInterfaceAttributes' not in zcl_data:
        zcl_data['attributeAccessInterfaceAttributes'] = {}

    attr_access_attrs = zcl_data['attributeAccessInterfaceAttributes']
    modified = False

    for attr_info in attributes_with_missing_types:
        cluster_name = attr_info['cluster']
        attr_name = attr_info['attribute']

        if cluster_name not in attr_access_attrs:
            attr_access_attrs[cluster_name] = [attr_name]
            modified = True
            print(f"Added new cluster '{cluster_name}' with attribute '{attr_name}' (type: {attr_info['type']})")
        else:
            if attr_name not in attr_access_attrs[cluster_name]:
                attr_access_attrs[cluster_name].append(attr_name)
                modified = True
                print(f"Added attribute '{attr_name}' to cluster '{cluster_name}' (type: {attr_info['type']})")

    return modified


def add_cluster_to_zcl(zcl_base: Path, cluster_xml_paths: list, output: Path, matter_path: Path = DEFAULT_MATTER_PATH):
    """
    Add the cluster to the ZCL file.
    """

    try:
        with open(zcl_base, "r") as zcl_json_base:
            zcl_json = json.load(zcl_json_base)
    except IOError:
        raise RuntimeError(f"No such ZCL file: {zcl_base}")

    # Resolve output.parent to normalize the path and remove any '..' segments
    # This is needed for relative_to() to work correctly
    # If output is None, use zcl_base.parent as the base for relative paths
    output_parent = (output.parent.resolve() if output else zcl_base.parent.resolve())

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
            if not path == "./" and not path == "." and not path.is_relative_to(output_parent):
                roots_replaced.append(str(path.relative_to(output_parent, walk_up=True)))
                replace = True
        if replace:
            zcl_json["xmlRoot"] = roots_replaced

        # Add the relative path to manufacturers XML
        manufacturers_xml = zcl_base.parent.joinpath(Path(zcl_json.get("manufacturersXml"))).resolve()
        if not manufacturers_xml.parent.is_relative_to(output_parent):
            zcl_json.update({"manufacturersXml": str(manufacturers_xml.relative_to(output_parent, walk_up=True))})

    # Add the new clusters to the ZCL file
    for cluster in cluster_xml_paths:
        if not Path(cluster).exists():
            raise RuntimeError(f"No such cluster file: {cluster}")

        # Get cluster file name
        file = Path(cluster).name
        # Get relative path from the cluster file to the output file.
        relative_path = Path(cluster).absolute().parent.relative_to(output_parent, walk_up=True)

        # We need to add two things:
        # 1. The absolute path to the directory where the new xml file exists to the xmlRoot array.
        # 2. The new xml file name to the xmlFile array.
        if str(relative_path) not in zcl_json.get("xmlRoot"):
            zcl_json.get("xmlRoot").append(str(relative_path))
        if file not in zcl_json.get("xmlFile"):
            zcl_json.get("xmlFile").append(file)
            log.dbg(f"Successfully added {file}")

        # Add custom attributes from the XML file to the ZCL file
        add_custom_attributes_from_xml(Path(cluster), zcl_json, matter_path)

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
        parser.add_argument("--clusters", nargs='+',
                            help="Paths to the XML files that contain the custom cluster definitions")
        return parser

    def do_run(self, args, unknown_args) -> None:
        if not args.base:
            args.base = args.matter.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH)
        if not args.output:
            args.output = args.matter.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH)

        for cluster in args.clusters:
            if not Path(cluster).exists():
                log.err(f"No such cluster file: {cluster}")
                return

        add_cluster_to_zcl(args.base.absolute(), args.clusters, args.output.absolute())
