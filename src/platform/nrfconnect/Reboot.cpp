/*
 *
 *    Copyright (c) 2022 Project CHIP Authors
 *
 *    Licensed under the Apache License, Version 2.0 (the "License");
 *    you may not use this file except in compliance with the License.
 *    You may obtain a copy of the License at
 *
 *        http://www.apache.org/licenses/LICENSE-2.0
 *
 *    Unless required by applicable law or agreed to in writing, software
 *    distributed under the License is distributed on an "AS IS" BASIS,
 *    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *    See the License for the specific language governing permissions and
 *    limitations under the License.
 */

#include "Reboot.h"

#include <lib/support/TypeTraits.h>

#include <zephyr/sys/reboot.h>

#if !(defined(CONFIG_ARCH_POSIX) || defined(CONFIG_SOC_SERIES_NRF54HX))
#include <hal/nrf_power.h>
#endif

#include <platform/SaveBootReasonDFUSuit.h>

namespace chip {
namespace DeviceLayer {

#if defined(CONFIG_ARCH_POSIX)

void Reboot(SoftwareRebootReason reason)
{
    sys_reboot(SYS_REBOOT_WARM);
}

SoftwareRebootReason GetSoftwareRebootReason()
{
    return SoftwareRebootReason::kOther;
}

#elif !(defined(CONFIG_SOC_SERIES_NRF54HX))

using RetainedReason = decltype(nrf_power_gpregret_get(NRF_POWER, 0));

constexpr RetainedReason EncodeReason(SoftwareRebootReason reason)
{
    // Set MSB to avoid collission with Zephyr's pre-defined reboot reasons.
    constexpr RetainedReason kCustomReasonFlag = 0x80;

    return static_cast<RetainedReason>(reason) | kCustomReasonFlag;
}

void Reboot(SoftwareRebootReason reason)
{
    const RetainedReason retainedReason = EncodeReason(reason);

    nrf_power_gpregret_set(NRF_POWER, 0, retainedReason);

    sys_reboot(retainedReason);
}

SoftwareRebootReason GetSoftwareRebootReason()
{
    switch (nrf_power_gpregret_get(NRF_POWER, 0))
    {
    case EncodeReason(SoftwareRebootReason::kSoftwareUpdate):
        nrf_power_gpregret_set(NRF_POWER, 0, 0);
        return SoftwareRebootReason::kSoftwareUpdate;
    default:
        return SoftwareRebootReason::kOther;
    }
}

#else

using RetainedReason = decltype(getSoftwareRebootReasonSUIT());

constexpr RetainedReason EncodeReason(SoftwareRebootReason reason)
{
    // Set MSB to avoid collission with Zephyr's pre-defined reboot reasons.
    constexpr RetainedReason kCustomReasonFlag = 0x80;

    return static_cast<RetainedReason>(reason) | kCustomReasonFlag;
}

void SetSoftwareRebootReason(SoftwareRebootReason reason)
{
    const RetainedReason retainedReason = EncodeReason(reason);
    setSoftwareRebootReasonSUIT(retainedReason);
}

SoftwareRebootReason GetSoftwareRebootReason()
{
    switch (getSoftwareRebootReasonSUIT())
    {
    case EncodeReason(SoftwareRebootReason::kSoftwareUpdate):
        setSoftwareRebootReasonSUIT(0);
        return SoftwareRebootReason::kSoftwareUpdate;
    default:
        return SoftwareRebootReason::kOther;
    }
}
#endif

} // namespace DeviceLayer
} // namespace chip
