# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
from pathlib import Path

from textwrap import dedent

from west.commands import WestCommand
from west import log

from zap_common import existing_file_path, existing_dir_path, find_zap, ZapInstaller, DEFAULT_MATTER_PATH, DEFAULT_ZCL_JSON_RELATIVE_PATH, DEFAULT_APP_TEMPLATES_RELATIVE_PATH, update_zcl_in_zap
from zap_append import add_cluster_to_zcl


class ZapGui(WestCommand):

    def __init__(self):
        super().__init__(
            'zap-gui',
            'Run Matter ZCL Advanced Platform (ZAP) GUI',
            dedent('''
            Run Matter ZCL Advanced Platform (ZAP) GUI.

            The ZAP GUI in a node.js tool for configuring the data model
            of a Matter application, which defines clusters, commands,
            attributes and events enabled for the given application.'''))

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.description)
        parser.add_argument('-z', '--zap-file', type=existing_file_path,
                            help='Path to data model configuration file (*.zap)')
        parser.add_argument('-j', '--zcl-json', type=str,
                            help='Path to data model definition file (zcl.json). If new clusters are added using --clusters, the new zcl.json file will be created and used.')
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help=f'Path to Matter SDK. Default is set to {DEFAULT_MATTER_PATH}')
        parser.add_argument('--clusters', nargs='+',
                            help="Paths to the XML files that contain the external cluster definitions")
        parser.add_argument('-c', '--cache', type=Path,
                            help='Path to the custom cache directory. If not provided a temporary directory will be used and cleared after the usage.')
        return parser

    def do_run(self, args, unknown_args):
        default_zcl_path = args.matter_path.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH)

        zap_file_path = args.zap_file or find_zap()
        zcl_json_path = Path(args.zcl_json).absolute() if args.zcl_json else default_zcl_path

        if args.clusters:
            # If the user provided the clusters and the zcl.json file provided by -j argument does not exist
            # we will create a new zcl.json file according to the base zcl.json file in default_zcl_path.
            # If the provided zcl.json file exists, we will use it as a base and update with a new cluster.
            base_zcl = zcl_json_path if zcl_json_path.exists() else default_zcl_path
            add_cluster_to_zcl(base_zcl, args.clusters, zcl_json_path)
        elif not zcl_json_path.exists():
            # If clusters are not provided, but user provided a zcl.json file we need to check whether the file exists.
            log.err(f"ZCL file not found: {zcl_json_path}")
            return

        app_templates_path = args.matter_path.joinpath(DEFAULT_APP_TEMPLATES_RELATIVE_PATH)

        log.inf(f"Using ZAP file: {zap_file_path}")
        log.inf(f"Using ZCL file: {zcl_json_path}")
        log.inf(f"Using app templates: {app_templates_path.absolute()}")

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()

        # The zcl.json path in the .zap file must be the same as the one provided by the user
        # If not, update the .zap file with the new relative path to the zcl.json file.
        # After that we must clear the ZAP cache.
        was_updated = update_zcl_in_zap(zap_file_path, zcl_json_path, app_templates_path)
        if args.cache and was_updated:
            log.wrn("ZCL file path in the ZAP file has been updated. The ZAP cache must be cleared to use it.")

        cmd = [zap_installer.get_zap_path()]
        cmd += [zap_file_path] if zap_file_path else []
        cmd += ["--zcl", zcl_json_path.absolute()]
        cmd += ["--gen", app_templates_path.absolute()]
        if args.cache:
            cmd += ["--stateDirectory", args.cache.absolute()]
        else:
            cmd += ["--tempState"]
        self.check_call([str(x) for x in cmd])
