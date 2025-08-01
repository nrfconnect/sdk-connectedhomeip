#!/usr/bin/env python3
#
#    Copyright (c) 2021 Project CHIP Authors
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
#

import argparse
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional

from clang_format import getClangFormatBinary
from zap_execution import ZapTool

# TODO: Can we share this constant definition with zap_regen_all.py?
DEFAULT_DATA_MODEL_DESCRIPTION_FILE = 'src/app/zap-templates/zcl/zcl.json'


@dataclass
class CmdLineArgs:
    zapFile: Optional[str]
    zclFile: str
    templateFile: str
    outputDir: str
    runBootstrap: bool
    parallel: bool = True
    prettify_output: bool = True
    version_check: bool = True
    lock_file: Optional[str] = None
    delete_output_dir: bool = False
    matter_file_name: Optional[str] = None


CHIP_ROOT_DIR = os.path.realpath(
    os.path.join(os.path.dirname(__file__), '../../..'))


def checkPythonVersion():
    if sys.version_info[0] < 3:
        print('Must use Python 3. Current version is ' +
              str(sys.version_info[0]))
        exit(1)


def checkFileExists(path):
    if not os.path.isfile(path):
        print('Error: ' + path + ' does not exists or is not a file.')
        exit(1)


def checkDirExists(path):
    if not os.path.isdir(path):
        print('Error: ' + path + ' does not exists or is not a directory.')
        exit(1)


def getFilePath(name, prefix_chip_root_dir=True):
    if prefix_chip_root_dir:
        fullpath = os.path.join(CHIP_ROOT_DIR, name)
    else:
        fullpath = name
    checkFileExists(fullpath)
    return fullpath


def getDirPath(name):
    fullpath = os.path.join(CHIP_ROOT_DIR, name)
    checkDirExists(fullpath)
    return fullpath


def detectZclFile(zapFile):
    print(f"Searching for zcl file from {zapFile}")

    prefix_chip_root_dir = True
    path = DEFAULT_DATA_MODEL_DESCRIPTION_FILE

    if zapFile:
        data = json.load(open(zapFile))
        for package in data["package"]:
            if package["type"] != "zcl-properties":
                continue

            prefix_chip_root_dir = (package["pathRelativity"] != "resolveEnvVars")
            # found the right path, try to figure out the actual path
            if package["pathRelativity"] == "relativeToZap":
                path = os.path.abspath(os.path.join(
                    os.path.dirname(zapFile), package["path"]))
            elif package["pathRelativity"] == "resolveEnvVars":
                path = os.path.expandvars(package["path"])
            else:
                path = package["path"]

    return getFilePath(path, prefix_chip_root_dir)


def runArgumentsParser() -> CmdLineArgs:
    # By default generate the idl file only. This will get moved from the
    # output directory into the zap file directory automatically.
    #
    # All the rest of the files (app-templates.json) are generally built at
    # compile time.
    default_templates = 'src/app/zap-templates/matter-idl-server.json'

    parser = argparse.ArgumentParser(
        description='Generate artifacts from .zapt templates')
    parser.add_argument('zap', nargs="?", default=None, help='Path to the application .zap file')
    parser.add_argument('-t', '--templates', default=default_templates,
                        help='Path to the .zapt templates records to use for generating artifacts (default: "' + default_templates + '")')
    parser.add_argument('-z', '--zcl',
                        help='Path to the zcl templates records to use for generating artifacts (default: autodetect read from zap file)')
    parser.add_argument('-o', '--output-dir', default=None,
                        help='Output directory for the generated files (default: a temporary directory in out)')
    parser.add_argument('-m', '--matter-file-name', default=None,
                        help='Where to copy any generated .matter file')
    parser.add_argument('--run-bootstrap', default=None, action='store_true',
                        help='Automatically run ZAP bootstrap. By default the bootstrap is not triggered')
    parser.add_argument('--parallel', action='store_true')
    parser.add_argument('--no-parallel', action='store_false', dest='parallel')
    parser.add_argument('--lock-file', help='serialize zap invocations by using the specified lock file.')
    parser.add_argument('--prettify-output', action='store_true')
    parser.add_argument('--no-prettify-output',
                        action='store_false', dest='prettify_output')
    parser.add_argument('--version-check', action='store_true')
    parser.add_argument('--no-version-check',
                        action='store_false', dest='version_check')
    parser.add_argument('--keep-output-dir', action='store_true',
                        help='Keep any created output directory. Useful for temporary directories.')
    parser.set_defaults(parallel=True)
    parser.set_defaults(prettify_output=True)
    parser.set_defaults(version_check=True)
    parser.set_defaults(lock_file=None)
    parser.set_defaults(keep_output_dir=False)
    parser.set_defaults(matter_file_name=None)
    args = parser.parse_args()

    delete_output_dir = False
    if args.output_dir:
        output_dir = args.output_dir
    elif args.templates == default_templates:
        output_dir = tempfile.mkdtemp(prefix='zapgen')
        delete_output_dir = not args.keep_output_dir
    else:
        output_dir = ''

    if args.zap:
        zap_file = getFilePath(args.zap)
    else:
        zap_file = None

    if args.zcl:
        zcl_file = getFilePath(args.zcl)
    else:
        zcl_file = detectZclFile(zap_file)

    templates_file = getFilePath(args.templates)
    output_dir = getDirPath(output_dir)

    if args.matter_file_name:
        matter_file_name = getFilePath(args.matter_file_name)
    else:
        matter_file_name = None

    return CmdLineArgs(
        zap_file, zcl_file, templates_file, output_dir, args.run_bootstrap,
        parallel=args.parallel,
        prettify_output=args.prettify_output,
        version_check=args.version_check,
        lock_file=args.lock_file,
        delete_output_dir=delete_output_dir,
        matter_file_name=matter_file_name,
    )


def matterPathFromZapPath(zap_config_path):
    if not zap_config_path:
        return None

    target_path = zap_config_path.replace(".zap", ".matter")
    if not target_path.endswith(".matter"):
        # We expect "something.zap" and don't handle corner cases of
        # multiple extensions. This is to work with existing codebase only
        raise Exception("Unexpected input zap file  %s" % zap_config_path)

    return target_path


def extractGeneratedIdl(output_dir, matter_name):
    """Find a file Clusters.matter in the output directory and
       place it along with the input zap file.

       Intent is to make the "zap content" more humanly understandable.
    """
    idl_path = os.path.join(output_dir, "Clusters.matter")
    if not os.path.exists(idl_path):
        return

    shutil.move(idl_path, matter_name)


def runGeneration(cmdLineArgs):
    zap_file = cmdLineArgs.zapFile
    zcl_file = cmdLineArgs.zclFile
    templates_file = cmdLineArgs.templateFile
    output_dir = cmdLineArgs.outputDir
    parallel = cmdLineArgs.parallel

    tool = ZapTool()

    if cmdLineArgs.version_check:
        tool.version_check()

    args = ['-z', zcl_file, '-g', templates_file, '-o', output_dir]

    if zap_file:
        args.append('-i')
        args.append(zap_file)

    if parallel:
        # Parallel-compatible runs will need separate state
        args.append('--tempState')

    tool.run('generate', *args)

    if cmdLineArgs.matter_file_name:
        matter_name = cmdLineArgs.matter_file_name
    else:
        matter_name = matterPathFromZapPath(zap_file)

    if matter_name:
        extractGeneratedIdl(output_dir, matter_name)


def expandPlaceholderWildcards(path: str) -> Generator[str, None, None]:
    """
    Generates expanded path lists from ZAP output paths.
    ZAP allows to use iterators (see https://github.com/project-chip/zap/blob/master/docs/sdk-integration.md#individual-template)
    and then paths may include placeholders such as '{name}'.

    If such placehoders exist in `path` this method will do a filesystem glob
    to select the actual outputs.
    """
    if '{' not in path:
        yield path
        return

    while '{' in path:
        s = path.find('{')
        e = path.find('}')
        path = path[:s] + '*' + path[e+1:]

    # path is a glob target, expand it
    for result in glob.glob(path):
        yield result


def runClangPrettifier(templates_file, output_dir):
    listOfSupportedFileExtensions = [
        '.js', '.h', '.c', '.hpp', '.cpp', '.m', '.mm', '.ipp']

    try:
        jsonData = json.loads(Path(templates_file).read_text())
        outputs = [(os.path.join(output_dir, template['output']))
                   for template in jsonData['templates']]
        rawPaths = list(filter(lambda filepath: os.path.splitext(
            filepath)[1] in listOfSupportedFileExtensions, outputs))

        clangOutputs = []
        for path in rawPaths:
            clangOutputs.extend(expandPlaceholderWildcards(path))
        clangOutputs = list(set(clangOutputs))  # unique paths in case of glob overlap

        if clangOutputs:
            # NOTE: clang-format differs output in time. We generally would be
            #       compatible only with pigweed provided clang-format (which is
            #       tracking non-released clang).
            clang_format = getClangFormatBinary()
            args = [clang_format, '-i']
            args.extend(clangOutputs)
            subprocess.check_call(args)
            print('Formatted %d files using %s (%s)' %
                  (len(clangOutputs), clang_format, subprocess.check_output([clang_format, '--version'])))
            for outputName in clangOutputs:
                logging.debug("Formatted: %s", outputName)
    except subprocess.CalledProcessError as err:
        print('clang-format error: %s', err)


class LockFileSerializer:
    def __init__(self, path):
        self.lock_file_path = path
        self.lock_file = None

    def __enter__(self):
        if not self.lock_file_path:
            return

        self.lock_file = open(self.lock_file_path, 'wb')
        self._lock()

    def __exit__(self, *args):
        if not self.lock_file:
            return

        self._unlock()
        self.lock_file.close()
        self.lock_file = None

    def _lock(self):
        if sys.platform == 'linux' or sys.platform == 'darwin':
            import fcntl
            fcntl.lockf(self.lock_file, fcntl.LOCK_EX)
        else:
            print(f"Warning: lock does nothing on {sys.platform} platform")

    def _unlock(self):
        if sys.platform == 'linux' or sys.platform == 'darwin':
            import fcntl
            fcntl.lockf(self.lock_file, fcntl.LOCK_UN)
        else:
            print(f"Warning: unlock does nothing on {sys.platform} platform")


def main():
    checkPythonVersion()
    cmdLineArgs = runArgumentsParser()

    with LockFileSerializer(cmdLineArgs.lock_file) as _:
        if cmdLineArgs.runBootstrap:
            subprocess.check_call(getFilePath("scripts/tools/zap/zap_bootstrap.sh"), shell=True)

        # on 64 bit systems, allow maximum memory usage to go over 4GB (#15620)
        if sys.maxsize >= 2**32:
            os.environ["NODE_OPTIONS"] = "--max-old-space-size=8192"

        # `zap-cli` may extract things into a temporary directory. ensure extraction
        # does not conflict.
        with tempfile.TemporaryDirectory(prefix='zap') as temp_dir:
            old_temp = os.environ['TEMP'] if 'TEMP' in os.environ else None
            os.environ['TEMP'] = temp_dir

            runGeneration(cmdLineArgs)

            if old_temp:
                os.environ['TEMP'] = old_temp
            else:
                del os.environ['TEMP']

        # Post-process fixes: zap needs some fixes from
        # https://github.com/project-chip/zap/pull/1569
        #
        # While that is going on, we need to post-process outputs
        renames = {
            '../../clusters/Pm2.5ConcentrationMeasurement': '../../clusters/Pm25ConcentrationMeasurement',
        }
        for src, dest in renames.items():
            srcDir = f'{cmdLineArgs.outputDir}/{src}'
            if not os.path.exists(srcDir):
                continue
            if not os.path.exists(f'{cmdLineArgs.outputDir}/{dest}'):
                os.makedirs(f'{cmdLineArgs.outputDir}/{dest}')
            print(f"Moving files from {srcDir} INTO {cmdLineArgs.outputDir}/{dest}")
            # move all files
            for name in glob.glob(f'{srcDir}/*'):
                os.rename(name, f'{cmdLineArgs.outputDir}/{dest}/{os.path.basename(name)}')
            os.rmdir(srcDir)

    if cmdLineArgs.prettify_output:
        prettifiers = [
            runClangPrettifier,
        ]

        for prettifier in prettifiers:
            prettifier(cmdLineArgs.templateFile, cmdLineArgs.outputDir)

    if cmdLineArgs.delete_output_dir:
        shutil.rmtree(cmdLineArgs.outputDir)
    else:
        print("Files generated in: %s" % cmdLineArgs.outputDir)


if __name__ == '__main__':
    main()
