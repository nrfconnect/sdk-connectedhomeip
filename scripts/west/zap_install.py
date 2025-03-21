# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
from textwrap import dedent
from west.commands import WestCommand
from zap_common import existing_dir_path, ZapInstaller, DEFAULT_MATTER_PATH


class ZapInstall(WestCommand):

    def __init__(self):
        super().__init__(
            'zap-install',
            'Install Matter ZCL Advanced Platform (ZAP) GUI',
            dedent('''
            Install Matter ZCL Advanced Platform (ZAP) GUI.

            The ZAP GUI in a node.js tool for configuring the data model
            of a Matter application, which defines clusters, commands,
            attributes and events enabled for the given application.'''))

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.description)
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help=f'Path to Matter SDK. Default is set to {DEFAULT_MATTER_PATH}')
        return parser

    def do_run(self, args, unknown_args):
        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()
