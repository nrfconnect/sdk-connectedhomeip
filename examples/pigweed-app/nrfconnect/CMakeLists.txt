#
#    Copyright (c) 2020 Project CHIP Authors
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
cmake_minimum_required(VERSION 3.13.1)

get_filename_component(CHIP_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/third_party/connectedhomeip REALPATH)
get_filename_component(NRFCONNECT_COMMON ${CHIP_ROOT}/examples/platform/nrfconnect REALPATH)
set(PIGWEED_ROOT "${CHIP_ROOT}/third_party/pigweed/repo")

if(${BOARD} STREQUAL "nrf52840dongle_nrf52840")
    list(INSERT OVERLAY_CONFIG 0 ${CHIP_ROOT}/config/nrfconnect/app/overlay-usb_support.conf)
endif()

# Load NCS/Zephyr build system
list(APPEND ZEPHYR_EXTRA_MODULES ${CHIP_ROOT}/config/nrfconnect/chip-module)
find_package(Zephyr HINTS $ENV{ZEPHYR_BASE})

project(chip-nrf52840-pigweed-example)

target_include_directories(app PRIVATE main/include 
                           ${NRFCONNECT_COMMON}/util/include
                           ${PIGWEED_ROOT}/pw_sys_io/public
                           ${PIGWEED_ROOT}/pw_span/public_overrides
                           ${PIGWEED_ROOT}/pw_span/public
                           ${PIGWEED_ROOT}/pw_polyfill/public
                           ${PIGWEED_ROOT}/pw_polyfill/standard_library_public
                           ${PIGWEED_ROOT}/pw_polyfill/public_overrides
                           ${PIGWEED_ROOT}/pw_status/public
                           ${PIGWEED_ROOT}/pw_preprocessor/public
                           ${NRFCONNECT_COMMON}/pw_sys_io/public)

target_sources(app PRIVATE main/main.cpp 
               ${NRFCONNECT_COMMON}/util/LEDWidget.cpp)
