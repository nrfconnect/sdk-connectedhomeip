# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import yaml
from west import log
from west.commands import CommandError, WestCommand
from zap_common import (DEFAULT_MATTER_PATH, ZapInstaller, existing_dir_path, existing_file_path, find_zap,
                        post_process_generated_files, update_zcl_in_zap)
from zap_sync import ZapSync

# fmt: off
scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scripts_dir)
sys.path.append(os.path.join(scripts_dir, 'tools'))
from tools.zap_regen_all import JinjaCodegenTarget, ZAPGenerateTarget, ZapInput

# fmt: on

ZEPHYR_BASE = os.environ.get('ZEPHYR_BASE', "")


@dataclass
class ZapFile:
    name: str = ""
    zap_file: Path = None
    full: bool = False
    zcl_file: Path = None


class ZapGenerate(WestCommand):

    def __init__(self):
        super().__init__(
            'zap-generate',  # gets stored as self.name
            'Generate Matter data model files with ZAP',  # self.help
            # self.description:
            dedent('''
            Generate Matter data model files with the use of ZAP Tool
            based on the .zap template file defined for your application.'''))

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.description)
        parser.add_argument('-z', '--zap-file', type=existing_file_path,
                            help='Path to data model configuration file (*.zap)')
        parser.add_argument('-o', '--output', type=existing_dir_path,
                            help='Path where to store the generated files')
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help='Path to Matter SDK')
        parser.add_argument('-f', '--full', action='store_true', help='Generate full data model files')
        parser.add_argument('-k', '--keep-previous', action='store_true', help='Keep previously generated files')
        parser.add_argument('-j', '--zcl', type=existing_file_path, help='Generate clusters from zcl.json')
        parser.add_argument('-y', '--yaml', type=existing_file_path,
                            help='Yaml file containing list of zap files to be used for generation. The file must contain the first entry as "base_dir" which is the relative path to the ZEPHYR_BASE directory. Then each other entry must contain the "name", "zap_file" and optionally "full" and "zcl_file"')
        return parser

    def build_command(self, zap_file_path, output_path, templates_path=None):
        if templates_path is None:
            # Generate the .matter file from the .zap file
            cmd = [sys.executable, self.zap_generate_path, zap_file_path, "-o", output_path]
        else:
            # Generate source files from the .zap file
            cmd = [sys.executable, self.zap_generate_path, zap_file_path, "-o", output_path, "-t", templates_path]
        return [str(x) for x in cmd]

    def do_run(self, args, unknown_args):
        self.zap_generate_path = args.matter_path / "scripts/tools/zap/generate.py"
        app_templates_path = args.matter_path / "src/app/zap-templates/app-templates.json"

        if args.yaml and args.zap_file:
            raise CommandError("Cannot use both -y and -z at the same time")

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()

        # make sure that the generate.py script uses the proper zap_cli binary (handled by west)
        os.environ["ZAP_INSTALL_PATH"] = str(zap_installer.get_zap_cli_path().parent.absolute())

        zap_files: list[ZapFile] = []

        # Load the yaml file
        if args.yaml:
            with open(args.yaml, 'r') as f:
                zaps = yaml.load(f, Loader=yaml.FullLoader)
                base_dir = zaps[0].get('base_dir', "")
                if not base_dir:
                    raise CommandError("base_dir is not set in the yaml file")

                for zap in zaps:
                    if zap.get('zap_file'):
                        name = zap.get('name', "")
                        zap_file = Path(ZEPHYR_BASE, base_dir, zap.get('zap_file', ""))
                        if not zap_file.exists():
                            raise CommandError(f"ZAP file {zap_file} does not exist")
                        zcl_file = Path(ZEPHYR_BASE, base_dir, zap.get('zcl_file', "")) if zap.get('zcl_file', "") else None
                        if zcl_file and not zcl_file.exists():
                            raise CommandError(f"ZCL file {zcl_file} does not exist")
                        full = zap.get('full', False)
                        clusters = None

                        if full:
                            clusters = [Path(ZEPHYR_BASE, base_dir, cluster).absolute() for cluster in zap.get('clusters', [])]
                            if not all(cluster.exists() for cluster in clusters):
                                raise CommandError("Some cluster files do not exist: " +
                                                   ", ".join([cluster.absolute() for cluster in clusters if not cluster.exists()]))

                        # Prepare arguments for ZapUpdate
                        zap_sync_args = argparse.Namespace(
                            zap_file=zap_file,
                            zcl_json=zcl_file,
                            clusters=clusters,
                            matter_path=args.matter_path
                        )

                        # Run zap_update to update and synchronize zap/zcl with clusters using the current Matter SDK
                        ZapSync().do_run(zap_sync_args, [])

                        zap_entry = ZapFile(name=name, zap_file=zap_file, full=full, zcl_file=zcl_file)
                        zap_files.append(zap_entry)
        else:
            if args.zap_file:
                zap_file_path = args.zap_file.absolute()
            else:
                zap_file_path = find_zap()

            if not zap_file_path:
                raise CommandError("No valid .zap file provided")

            if args.zcl:
                zcl_file = args.zcl.absolute()
            elif (zap_file_path.parent / "zcl.json").exists():
                zcl_file = zap_file_path.parent / "zcl.json"
            else:
                zcl_file = None

            zap_files.append(ZapFile(name=zap_file_path.stem, zap_file=Path(
                zap_file_path), full=args.full, zcl_file=zcl_file))

        # Generate the zap file
        for zap in zap_files:
            if args.output:
                output_path = args.output.absolute()
            else:
                output_path = zap.zap_file.parent / "zap-generated"

            # Make sure that output directory exists
            output_path.mkdir(exist_ok=True)

            if not args.keep_previous:
                self.clear_generated_files(output_path)
                if args.full:
                    log.inf(f"Clearing output directory: {output_path}")
                    shutil.rmtree(output_path)
                    output_path.mkdir(exist_ok=True)

            log.inf('----------------------------------------------------------')
            log.inf(f"Generating source files for: {zap.name}")
            log.inf(f"ZAP file: {zap.zap_file}")
            log.inf(f"Output path: {output_path}")
            log.inf(f"App templates path: {app_templates_path}")
            log.inf(f"Full: {args.full}")
            log.inf(f"Keep previous: {args.keep_previous}")
            log.inf(f"ZCL file: {zap.zcl_file}")
            log.inf('----------------------------------------------------------')

            # Generate source files
            self.check_call(self.build_command(zap.zap_file, output_path, app_templates_path))

            # Generate .matter file
            self.check_call(self.build_command(zap.zap_file, output_path))

            if args.full or zap.full:
                # Full build is about generating an apropertiate Matter data model files in a specific directory layout.
                # Currently, we must align to the following directory layout:
                # sample/
                #   |_ src/
                #     |_ default_zap/
                #       |_ zcl.xml
                #       |_ sample.zap
                #       |_ sample.matter
                #       |_ clusters/
                #         |_ *All clusters*
                #       |_ app-common
                #         |_ zap-generated/
                #           |_ attributes/
                #           |_ ids/
                #
                # Generation of the full data model files consist of three steps:
                # 1. ZAPGenerateTarget generates "app-common/zap-generated" directory and a part of "clusters/" directory.
                #    It generates Attrbutes.h/cpp, Events.h/cpp, Commands.h/cpp files for each cluster.
                # 2. JinjaCodegenTarget generates "clusters/" directory.
                #    It generates "AttributeIds.h/cpp", "EventIds.h", "CommandIds.h" files for each cluster.
                #    It generates also BUILD.gn files that are used to configure build system.
                #
                # Currently, we must call JinjaCodegenTarget twice:
                # - for all clusters using controller-clusters.matter file to generate all clusters defined in the Matter spec.
                # - for the sample's .matter file to generate the new data model files that are not defined in the Matter spec.
                #
                # To generate the full data model files, we utilizes classes defined in the matter/scripts/tools/zap_regen_all.py file.
                # These classes are supposed to be called from the matter root directory, so we must temporarily change the current working directory to the matter root directory.
                zcl_file = zap.zcl_file or zap.zap_file.parent / "zcl.json"
                zap_input = ZapInput.FromPropertiesJson(zcl_file)
                template = 'src/app/common/templates/templates.json'
                zap_output_dir = output_path / 'app-common' / 'zap-generated'
                codegen_output_dir = output_path / 'clusters'

                # Update the zcl in zap file if needed
                # We need to do this in case the zap gui was not called before.
                update_zcl_in_zap(zap.zap_file, zcl_file, app_templates_path)

                # Temporarily change directory to matter_path so JinjaCodegenTarget and ZAPGenerateTarget can find their scripts
                original_cwd = os.getcwd()
                os.chdir(args.matter_path)
                try:
                    ZAPGenerateTarget(zap_input, template=template, output_dir=zap_output_dir).generate()
                    JinjaCodegenTarget(
                        generator="cpp-sdk",
                        idl_path="src/controller/data_model/controller-clusters.matter",
                        output_directory=codegen_output_dir).generate()
                    JinjaCodegenTarget(
                        generator="cpp-sdk",
                        idl_path=zap.zap_file.with_suffix(".matter"),
                        output_directory=codegen_output_dir).generate()
                finally:
                    # Restore original working directory
                    os.chdir(original_cwd)

            # Post-process the generated files
            post_process_generated_files(output_path.parent)

            log.inf(f"Done. Files generated in {output_path}")

    def clear_generated_files(self, path: Path):
        log.inf("Clearing previously generated files:")
        for file in path.iterdir():
            if file.is_file():
                with open(file, 'r') as f:
                    for line in f.readlines():
                        if "// THIS FILE IS GENERATED BY ZAP" in line:
                            log.inf(f"\tRemoving {file}")
                            break
                file.unlink()
