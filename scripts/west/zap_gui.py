# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse

from textwrap import dedent

from west.commands import WestCommand

from zap_common import existing_file_path, existing_dir_path, find_zap, ZapInstaller, DEFAULT_MATTER_PATH


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
        parser.add_argument('-j', '--zcl-json', type=existing_file_path,
                            help='Path to data model definition file (zcl.json)')
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help='Path to Matter SDK')
        return parser

    def do_run(self, args, unknown_args):
        if args.zap_file:
            zap_file_path = args.zap_file
        else:
            zap_file_path = find_zap()

        if args.zcl_json:
            zcl_json_path = args.zcl_json.absolute()
        else:
            zcl_json_path = args.matter_path / 'src/app/zap-templates/zcl/zcl.json'

        app_templates_path = args.matter_path / 'src/app/zap-templates/app-templates.json'

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()
        zap_cache_path = zap_installer.get_install_path() / ".zap"

        cmd = [zap_installer.get_zap_path()]
        cmd += [zap_file_path] if zap_file_path else []
        cmd += ["--zcl", zcl_json_path]
        cmd += ["--gen", app_templates_path]
        cmd += ["--stateDirectory", zap_cache_path]

        self.check_call([str(x) for x in cmd])
